"""Tests for orc/dispatcher.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from conftest import FakePopen, make_msg
from typer.testing import CliRunner

import orc.cli.merge as _merge_mod
import orc.cli.run as _run_mod
import orc.config as _cfg
import orc.context as _ctx
import orc.dispatcher as _disp
import orc.git as _git
import orc.invoke as inv
import orc.main as m
import orc.telegram as tg
from orc.dispatcher import CLOSE_BOARD, QA_PASSED, Dispatcher
from orc.pool import AgentProcess
from orc.squad import SquadConfig

runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_agent(tmp_path: Path, *, role: str = "coder", task: str = "0001-foo.md") -> AgentProcess:
    return AgentProcess(
        agent_id=f"{role}-1",
        role=role,
        model="copilot",
        task_name=task,
        process=FakePopen(),
        worktree=tmp_path,
        log_path=tmp_path / f"{role}.log",
        log_fh=None,
    )


def _minimal_squad(**kw) -> SquadConfig:
    defaults = dict(
        planner=1,
        coder=1,
        qa=1,
        timeout_minutes=60,
        name="test",
        description="",
        _models={},
    )
    defaults.update(kw)
    return SquadConfig(**defaults)


def _make_callbacks(
    tmp_path: Path,
    *,
    get_messages=None,
    get_open_tasks=None,
    derive_task_state=None,
    spawn_fn=None,
    wait_for_human_reply=None,
    get_pending_visions=None,
    get_pending_reviews=None,
) -> _disp.DispatchCallbacks:
    """Return a fully-wired DispatchCallbacks for Dispatcher tests."""
    board_dir = tmp_path / ".orc" / "work"
    board_dir.mkdir(parents=True, exist_ok=True)
    (board_dir / "board.yaml").write_text("counter: 0\nopen: []\ndone: []\n")

    def _spawn(ctx, cwd, model, log):
        return FakePopen(), None

    return _disp.DispatchCallbacks(
        get_messages=get_messages or (lambda: []),
        get_open_tasks=get_open_tasks or (lambda: []),
        has_unresolved_block=lambda msgs: (None, None),
        derive_task_state=derive_task_state or (lambda t: ("coder", "ready")),
        ensure_dev_worktree=lambda: tmp_path,
        ensure_feature_worktree=lambda t: tmp_path,
        build_context=lambda role, agent_id, msgs, wt: ("model", "ctx"),
        boot_message_body=lambda: "booting",
        post_boot_message=lambda agent_id, body: None,
        spawn_fn=spawn_fn or _spawn,
        assign_task=lambda task, agent: None,
        unassign_task=lambda task: None,
        do_close_board=lambda task: None,
        merge_feature=lambda task: None,
        wait_for_human_reply=wait_for_human_reply or (lambda msgs, **kw: "reply"),
        post_resolved=lambda a, s, r: None,
        # Default to a non-empty pending visions list so that tests which
        # exercise the "no open tasks → dispatch planner" path continue to
        # work.  Tests that want truly-no-work behaviour must override this.
        get_pending_visions=get_pending_visions or (lambda: ["placeholder.md"]),
        get_pending_reviews=get_pending_reviews or (lambda: []),
    )


# ---------------------------------------------------------------------------
# Boot message sent before agent invocation
# ---------------------------------------------------------------------------


class TestBootMessageSentBeforeInvoke:
    def _common_patches(self, monkeypatch, tmp_path):
        """Patch git helpers so tests don't hit subprocess."""
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path)
        monkeypatch.setattr(_git, "_feature_branch_exists", lambda b: False)
        monkeypatch.setattr(_git, "_feature_has_commits_ahead_of_main", lambda b: False)
        monkeypatch.setattr(_git, "_feature_merged_into_dev", lambda b: False)
        monkeypatch.setattr(_git, "_last_feature_commit_message", lambda b: None)
        monkeypatch.setattr(_git, "_ensure_feature_worktree", lambda task: tmp_path)
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)

    def test_boot_message_sent(self, tmp_path, monkeypatch):
        """Orchestrator sends (boot) message before invoking the agent."""
        board = tmp_path / "board.yaml"
        board.write_text("counter: 1\nopen:\n  - name: 0001-foo.md\n")
        monkeypatch.setattr(_cfg, "BOARD_FILE", board)
        self._common_patches(monkeypatch, tmp_path)

        sent: list[str] = []
        monkeypatch.setattr(tg, "send_message", lambda text: sent.append(text))
        monkeypatch.setattr(tg, "get_messages", lambda: [])
        monkeypatch.setattr(_ctx, "build_agent_context", lambda *a, **kw: ("model", "ctx"))
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])
        monkeypatch.setattr(_merge_mod, "_rebase_dev_on_main", lambda *_: None)
        monkeypatch.setattr(inv, "spawn", lambda *a, **kw: (FakePopen(), None))
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        result = runner.invoke(m.app, ["run", "--maxloops", "1"])
        assert result.exit_code == 0
        assert len(sent) == 1
        assert "(boot)" in sent[0]
        assert "work/0001-foo.md" in sent[0]

    def test_boot_message_precedes_invoke(self, tmp_path, monkeypatch):
        """Boot message must be sent BEFORE spawn is called."""
        board = tmp_path / "board.yaml"
        board.write_text("counter: 1\nopen:\n  - name: 0001-foo.md\n")
        monkeypatch.setattr(_cfg, "BOARD_FILE", board)
        self._common_patches(monkeypatch, tmp_path)

        call_order: list[str] = []
        monkeypatch.setattr(tg, "send_message", lambda text: call_order.append("send"))
        monkeypatch.setattr(tg, "get_messages", lambda: [])
        monkeypatch.setattr(_ctx, "build_agent_context", lambda *a, **kw: ("model", "ctx"))
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])
        monkeypatch.setattr(_merge_mod, "_rebase_dev_on_main", lambda *_: None)
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        def fake_spawn(*a, **kw):
            call_order.append("invoke")
            return FakePopen(), None

        monkeypatch.setattr(inv, "spawn", fake_spawn)

        runner.invoke(m.app, ["run", "--maxloops", "1"])
        assert call_order == ["send", "invoke"]


# ---------------------------------------------------------------------------
# Blocked-state recovery
# ---------------------------------------------------------------------------


class TestBlockedResumption:
    def _blocked_msgs(self, agent_id: str) -> list[dict]:
        """agent_id should be in 'role-N' format, e.g. 'coder-1'."""
        return [make_msg(f"[{agent_id}](blocked) 2026-03-09T11:00:00Z: Need help.", ts=1000)]

    def _common_patches(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path)
        monkeypatch.setattr(_git, "_feature_branch_exists", lambda b: False)
        monkeypatch.setattr(_git, "_feature_has_commits_ahead_of_main", lambda b: False)
        monkeypatch.setattr(_git, "_feature_merged_into_dev", lambda b: False)
        monkeypatch.setattr(_git, "_last_feature_commit_message", lambda b: None)
        monkeypatch.setattr(_git, "_ensure_feature_worktree", lambda task: tmp_path)
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)

    def test_blocked_agent_resumes_after_reply(self, monkeypatch, tmp_path):
        board = tmp_path / "board.yaml"
        board.write_text("counter: 1\nopen:\n  - name: 0001-foo.md\n")
        monkeypatch.setattr(_cfg, "BOARD_FILE", board)
        self._common_patches(monkeypatch, tmp_path)

        blocked = self._blocked_msgs("coder-1")
        msg_iter = iter([blocked, []])
        monkeypatch.setattr(tg, "get_messages", lambda: next(msg_iter, []))

        invocations: list[str] = []
        monkeypatch.setattr(
            _ctx,
            "build_agent_context",
            lambda name, msgs, **kw: invocations.append(name) or ("model", "ctx"),
        )
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])
        monkeypatch.setattr(_merge_mod, "_rebase_dev_on_main", lambda *_: None)
        monkeypatch.setattr(tg, "send_message", lambda t: None)
        monkeypatch.setattr(_ctx, "wait_for_human_reply", lambda msgs, **kw: "Here's the fix.")
        monkeypatch.setattr(inv, "spawn", lambda *a, **kw: (FakePopen(), None))
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        monkeypatch.setattr(_run_mod, "logger", MagicMock())

        rc = runner.invoke(m.app, ["run", "--maxloops", "1"])
        assert rc.exit_code == 0
        assert invocations == ["coder"]

    def test_blocked_resumes_correct_agent(self, monkeypatch, tmp_path):
        """After a hard-block reply, the dispatcher routes to the correct role."""
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path)
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])
        monkeypatch.setattr(_merge_mod, "_rebase_dev_on_main", lambda *_: None)
        monkeypatch.setattr(tg, "send_message", lambda t: None)
        monkeypatch.setattr(_ctx, "wait_for_human_reply", lambda msgs, **kw: "Help.")
        monkeypatch.setattr(inv, "spawn", lambda *a, **kw: (FakePopen(), None))
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        # The planner-1 case has an empty board.  A vision doc is required so
        # the dispatcher has something for the planner to work on after the
        # hard-block reply; without it the loop would exit with "no pending work".
        (tmp_path / "vision").mkdir(exist_ok=True)
        (tmp_path / "vision" / "feature-x.md").write_text("# Feature X\n")

        cases = [
            (
                "planner-1",
                "counter: 1\nopen: []\n",
                {},
            ),
            (
                "coder-1",
                "counter: 1\nopen:\n  - name: 0001-foo.md\n",
                {
                    "_feature_branch_exists": False,
                    "_feature_has_commits_ahead_of_main": False,
                    "_feature_merged_into_dev": False,
                    "_last_feature_commit_message": None,
                },
            ),
            (
                "qa-1",
                "counter: 1\nopen:\n  - name: 0001-foo.md\n",
                {
                    "_feature_branch_exists": True,
                    "_feature_has_commits_ahead_of_main": True,
                    "_feature_merged_into_dev": False,
                    "_last_feature_commit_message": "feat: implement it",
                },
            ),
        ]

        for agent_id, board_content, git_map in cases:
            board = tmp_path / "board.yaml"
            board.write_text(board_content)
            monkeypatch.setattr(_cfg, "BOARD_FILE", board)
            monkeypatch.setattr(_git, "_ensure_feature_worktree", lambda task: tmp_path)
            monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)
            for attr, val in git_map.items():
                monkeypatch.setattr(_git, attr, lambda _b, v=val: v)

            blocked = self._blocked_msgs(agent_id)
            msg_iter = iter([blocked, []])
            monkeypatch.setattr(tg, "get_messages", lambda: next(msg_iter))

            invocations: list[str] = []
            monkeypatch.setattr(
                _ctx,
                "build_agent_context",
                lambda name, msgs, **kw: invocations.append(name) or ("model", "ctx"),
            )

            runner.invoke(m.app, ["run", "--maxloops", "1"])
            expected_role = agent_id.split("-")[0]
            assert invocations == [expected_role], (
                f"Expected {expected_role} to be dispatched for {agent_id} block"
            )

    def test_timeout_posts_telegram_message_and_exits(self, monkeypatch, tmp_path):
        board = tmp_path / "board.yaml"
        board.write_text("counter: 1\nopen:\n  - name: 0001-foo.md\n")
        monkeypatch.setattr(_cfg, "BOARD_FILE", board)
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path)
        monkeypatch.setattr(_git, "_feature_branch_exists", lambda b: False)
        monkeypatch.setattr(_git, "_feature_has_commits_ahead_of_main", lambda b: False)
        monkeypatch.setattr(_git, "_feature_merged_into_dev", lambda b: False)
        monkeypatch.setattr(_git, "_last_feature_commit_message", lambda b: None)
        monkeypatch.setattr(_git, "_ensure_feature_worktree", lambda task: tmp_path)
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)

        monkeypatch.setattr(tg, "get_messages", lambda: self._blocked_msgs("coder-1"))
        sent: list[str] = []
        monkeypatch.setattr(tg, "send_message", lambda t: sent.append(t))
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])
        monkeypatch.setattr(_merge_mod, "_rebase_dev_on_main", lambda *_: None)
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        def _timeout(msgs, **kw):
            raise TimeoutError("timed out")

        monkeypatch.setattr(_ctx, "wait_for_human_reply", _timeout)

        rc = runner.invoke(m.app, ["run", "--maxloops", "1"])
        assert rc.exit_code == 1
        assert len(sent) == 1
        assert "(blocked)" in sent[0]
        assert "Stopped" in sent[0]

    def test_planner_done_is_not_blocked(self, monkeypatch, tmp_path):
        """planner(done) is not a blocked state — git routes to planner (no tasks)."""
        board = tmp_path / "board.yaml"
        board.write_text("counter: 1\nopen: []\n")
        monkeypatch.setattr(_cfg, "BOARD_FILE", board)
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path)

        # A vision doc gives the planner something to plan (otherwise no dispatch).
        (tmp_path / "vision").mkdir(exist_ok=True)
        (tmp_path / "vision" / "feature-x.md").write_text("# Feature X\n")

        monkeypatch.setattr(_git, "_feature_branch_exists", lambda b: False)
        monkeypatch.setattr(_git, "_feature_has_commits_ahead_of_main", lambda b: False)
        monkeypatch.setattr(_git, "_feature_merged_into_dev", lambda b: False)
        monkeypatch.setattr(_git, "_last_feature_commit_message", lambda b: None)
        monkeypatch.setattr(_git, "_ensure_feature_worktree", lambda task: tmp_path)
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)

        done_msgs = [make_msg("[planner-1](done) 2026-03-09T10:00:00Z: All done.", ts=1000)]
        monkeypatch.setattr(tg, "get_messages", lambda: done_msgs)
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])
        monkeypatch.setattr(_merge_mod, "_rebase_dev_on_main", lambda *_: None)
        monkeypatch.setattr(tg, "send_message", lambda t: None)
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        invocations: list[str] = []
        monkeypatch.setattr(
            _ctx,
            "build_agent_context",
            lambda name, msgs, **kw: invocations.append(name) or ("model", "ctx"),
        )
        monkeypatch.setattr(inv, "spawn", lambda *a, **kw: (FakePopen(), None))

        wait_called: list[bool] = []
        monkeypatch.setattr(
            _ctx, "wait_for_human_reply", lambda msgs, **kw: wait_called.append(True) or ""
        )

        rc = runner.invoke(m.app, ["run", "--maxloops", "1"])
        _ = rc
        assert not wait_called
        assert invocations == ["planner"]


# ---------------------------------------------------------------------------
# Dispatcher unit tests
# ---------------------------------------------------------------------------


class TestDispatcherCoverage:
    def test_run_shutdown_signal_raises_exit_130(self, tmp_path, monkeypatch):
        """run() catches _ShutdownSignal and raises typer.Exit(130)."""
        import click

        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        cb = _make_callbacks(tmp_path)
        d = Dispatcher(_minimal_squad(), cb)

        def boom(maxloops):
            raise _disp._ShutdownSignal(2)

        d._loop = boom
        d._kill_all_and_unassign = MagicMock()
        with pytest.raises(click.exceptions.Exit) as exc_info:
            d.run()
        assert exc_info.value.exit_code == 130

    def test_do_merge_failure_logs_error(self, tmp_path, monkeypatch):
        """_do_merge: exception → error logged, no crash."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        cb = _make_callbacks(tmp_path)
        cb.merge_feature = lambda task: (_ for _ in ()).throw(RuntimeError("merge conflict"))
        d = Dispatcher(_minimal_squad(), cb)
        d._do_merge("0001-foo.md")  # should not raise

    def test_handle_watchdog_kills_agent(self, tmp_path, monkeypatch):
        """_handle_watchdog kills the agent and unassigns its task."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        unassigned = []
        cb = _make_callbacks(tmp_path, get_open_tasks=lambda: [])
        cb.unassign_task = lambda t: unassigned.append(t)
        d = Dispatcher(_minimal_squad(), cb)
        agent = _make_agent(tmp_path, role="coder")
        d.pool.add(agent)
        d._handle_watchdog(agent)
        assert "0001-foo.md" in unassigned

    def test_spawn_agent_raises_for_non_planner_without_task(self, tmp_path):
        cb = _make_callbacks(tmp_path)
        d = Dispatcher(_minimal_squad(), cb)
        with pytest.raises(ValueError, match="No worktree"):
            d._spawn_agent("coder", "coder-1", None, [])

    def test_spawn_agent_dry_run_prints(self, tmp_path, capsys):
        cb = _make_callbacks(tmp_path)
        d = Dispatcher(_minimal_squad(), cb, dry_run=True)
        d._spawn_agent("planner", "planner-1", None, [])
        captured = capsys.readouterr()
        assert "Would spawn" in captured.out

    def test_dispatch_soft_block_spawns_planner(self, tmp_path, monkeypatch):
        """Soft-blocked state → planner spawned to resolve."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        cb = _make_callbacks(tmp_path, get_open_tasks=lambda: [{"name": "0001-foo.md"}])
        cb.has_unresolved_block = lambda msgs: ("coder-1", "soft-blocked")
        d = Dispatcher(_minimal_squad(), cb)
        count = d._dispatch([])
        assert count >= 1

    def test_dispatch_skips_assigned_task(self, tmp_path):
        """Task already assigned → skipped."""
        cb = _make_callbacks(
            tmp_path,
            get_open_tasks=lambda: [{"name": "0001-foo.md", "assigned_to": "coder-1"}],
            derive_task_state=lambda t: ("coder", "reason"),
        )
        d = Dispatcher(_minimal_squad(), cb)
        count = d._dispatch([])
        assert count == 0

    def test_dispatch_close_board(self, tmp_path):
        """CLOSE_BOARD token → do_close_board called."""
        closed = []
        cb = _make_callbacks(
            tmp_path,
            get_open_tasks=lambda: [{"name": "0001-foo.md"}],
            derive_task_state=lambda t: (CLOSE_BOARD, "close"),
        )
        cb.do_close_board = lambda t: closed.append(t)
        d = Dispatcher(_minimal_squad(), cb)
        d._dispatch([])
        assert "0001-foo.md" in closed

    def test_dispatch_skips_unknown_token(self, tmp_path):
        """Unknown token → task skipped, dispatch returns 0."""
        cb = _make_callbacks(
            tmp_path,
            get_open_tasks=lambda: [{"name": "0001-foo.md"}],
            derive_task_state=lambda t: ("unknown_token", "reason"),
        )
        d = Dispatcher(_minimal_squad(), cb)
        assert d._dispatch([]) == 0

    def test_dispatch_skips_when_role_at_capacity(self, tmp_path):
        """Coder at capacity → skip."""
        cb = _make_callbacks(
            tmp_path,
            get_open_tasks=lambda: [{"name": "0001-foo.md"}],
            derive_task_state=lambda t: ("coder", "reason"),
        )
        d = Dispatcher(_minimal_squad(), cb)
        d.pool.add(_make_agent(tmp_path, role="coder"))  # coder-1 already running
        assert d._dispatch([]) == 0

    def test_loop_refreshes_messages_after_completion(self, tmp_path, monkeypatch):
        """Line 235: messages refreshed after hard-block handling."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        cb = _make_callbacks(tmp_path)
        d = Dispatcher(_minimal_squad(), cb, dry_run=True)

        block_returned = [False]

        def fake_has_unresolved_block(messages):
            if not block_returned[0]:
                block_returned[0] = True
                return ("planner-1", "blocked")
            return (None, None)

        d.cb.has_unresolved_block = fake_has_unresolved_block
        d._handle_hard_block = MagicMock()
        d._loop(maxloops=0)

        d._handle_hard_block.assert_called_once()

    def test_loop_watchdog_triggers(self, tmp_path, monkeypatch):
        """Watchdog kills stuck agents."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        class StuckPopen:
            returncode = None
            _context_tmp = None

            def poll(self):
                return None

            def kill(self):
                pass

            def wait(self, timeout=None):
                pass

        spawned = [False]

        def spawn_fn(ctx, cwd, model, log):
            if not spawned[0]:
                spawned[0] = True
                return StuckPopen(), None
            return FakePopen(), None

        squad = SquadConfig(
            planner=1,
            coder=1,
            qa=1,
            timeout_minutes=0,
            name="test",
            description="",
            _models={},
        )
        cb = _make_callbacks(tmp_path, spawn_fn=spawn_fn)
        d = Dispatcher(squad, cb)
        d.run(maxloops=2)

    def test_loop_dry_run_stops_after_one_cycle(self, tmp_path, monkeypatch):
        """Dry-run breaks after first dispatch."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        cb = _make_callbacks(tmp_path)
        d = Dispatcher(_minimal_squad(), cb, dry_run=True)
        d.run(maxloops=0)
        assert d._total_spawned == 1

    def test_handle_hard_block_posts_resolved(self, tmp_path):
        """_handle_hard_block posts resolved after human reply."""
        resolved = []
        cb = _make_callbacks(
            tmp_path,
            wait_for_human_reply=lambda msgs, **kw: "Here is the answer.",
        )
        cb.post_resolved = lambda a, s, r: resolved.append((a, s, r))
        d = Dispatcher(_minimal_squad(), cb)
        d._handle_hard_block("coder-1", [])
        assert len(resolved) == 1
        assert resolved[0][0] == "coder-1"

    def test_dispatch_qa_passed_queues_merge(self, tmp_path, monkeypatch):
        """QA_PASSED token → task added to merge queue."""
        cb = _make_callbacks(
            tmp_path,
            get_open_tasks=lambda: [{"name": "0001-foo.md"}],
            derive_task_state=lambda t: (QA_PASSED, "qa passed"),
        )
        d = Dispatcher(_minimal_squad(), cb)
        d._dispatch([])
        assert "0001-foo.md" in d._merge_queue

    def test_handle_completion_posts_resolved_for_soft_block_planner(self, tmp_path, monkeypatch):
        """After planner resolves soft-block, post_resolved is called."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        resolved = []
        cb = _make_callbacks(tmp_path)
        cb.post_resolved = lambda a, s, r: resolved.append((a, s, r))

        d = Dispatcher(_minimal_squad(), cb)
        d._resolving_soft_block = ("coder-1", "soft-blocked")

        agent = AgentProcess(
            agent_id="planner-1",
            role="planner",
            model="copilot",
            task_name=None,
            process=FakePopen(),
            worktree=tmp_path,
            log_path=tmp_path / "log",
            log_fh=None,
        )
        d.pool.add(agent)
        d._handle_completion(agent, 0, [])
        assert len(resolved) == 1

    def test_handle_completion_failed_agent_unassigns(self, tmp_path, monkeypatch):
        """Non-zero exit → task unassigned."""
        unassigned = []
        cb = _make_callbacks(tmp_path)
        cb.unassign_task = lambda t: unassigned.append(t)
        d = Dispatcher(_minimal_squad(), cb)
        agent = _make_agent(tmp_path, role="coder")
        d.pool.add(agent)
        d._handle_completion(agent, 1, [])
        assert "0001-foo.md" in unassigned

    def test_loop_merge_queue_drained(self, tmp_path, monkeypatch):
        """Merge queue drained in loop."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        merged = []
        cb = _make_callbacks(tmp_path)
        cb.merge_feature = lambda t: merged.append(t)
        d = Dispatcher(_minimal_squad(), cb)
        d._merge_queue.append("0001-foo.md")
        d.run(maxloops=1)
        assert "0001-foo.md" in merged


# ---------------------------------------------------------------------------
# Coverage gap tests
# ---------------------------------------------------------------------------


class TestDispatcherInternalCoverage:
    def test_has_pending_work_open_tasks_returns_true(self, tmp_path):
        """has_pending_work returns True when open tasks exist."""
        cb = _make_callbacks(
            tmp_path,
            get_open_tasks=lambda: [{"name": "0001-foo.md"}],
        )
        assert Dispatcher.has_pending_work(cb, []) is True

    def test_has_pending_work_blocked_returns_true(self, tmp_path):
        """has_pending_work returns True when blocked agent exists."""
        cb = _make_callbacks(
            tmp_path,
            get_open_tasks=lambda: [],
        )
        cb.has_unresolved_block = lambda msgs: ("agent-42", "blocked")
        assert Dispatcher.has_pending_work(cb, []) is True

    def test_has_pending_work_no_work_returns_false(self, tmp_path):
        """has_pending_work returns False when nothing pending."""
        cb = _make_callbacks(
            tmp_path,
            get_open_tasks=lambda: [],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: [],
        )
        cb.has_unresolved_block = lambda msgs: (None, None)
        assert Dispatcher.has_pending_work(cb, []) is False

    def test_kill_all_and_unassign(self, tmp_path):
        """Lines 493-496: _kill_all_and_unassign unassigns tasks and kills agents."""
        unassigned = []
        cb = _make_callbacks(tmp_path)
        cb.unassign_task = lambda t: unassigned.append(t)
        d = Dispatcher(_minimal_squad(), cb)
        agent = _make_agent(tmp_path, role="coder")
        d.pool.add(agent)
        d._kill_all_and_unassign()
        assert "0001-foo.md" in unassigned

    def test_shutdown_handler_raises_signal(self, tmp_path):
        """Line 499: _shutdown_handler raises _ShutdownSignal."""
        import pytest

        cb = _make_callbacks(tmp_path)
        d = Dispatcher(_minimal_squad(), cb)
        with pytest.raises(_disp._ShutdownSignal):
            d._shutdown_handler(15, None)

    def test_cleanup_context_tmp_deletes_file(self, tmp_path):
        """Lines 515-517: _cleanup_context_tmp deletes the temp file."""
        tmp_file = tmp_path / "ctx.tmp"
        tmp_file.write_text("context data")

        class FakeProc:
            _context_tmp = str(tmp_file)

        _disp._cleanup_context_tmp(FakeProc())
        assert not tmp_file.exists()

    def test_cleanup_context_tmp_no_attr(self, tmp_path):
        """_cleanup_context_tmp is a no-op when _context_tmp not present."""

        class FakeProc:
            pass

        _disp._cleanup_context_tmp(FakeProc())  # should not raise


class TestDispatchCallbacksOptional:
    """Tests for the optional on_agent_start / on_agent_done hooks."""

    def test_on_agent_start_called_after_spawn(self, tmp_path):
        """on_agent_start receives the AgentProcess just added to the pool."""
        started = []

        def _spawn(ctx, cwd, model, log):
            return FakePopen(), None

        cb = _make_callbacks(
            tmp_path,
            get_open_tasks=lambda: [],
            spawn_fn=_spawn,
        )
        cb.on_agent_start = lambda agent: started.append(agent.agent_id)

        d = Dispatcher(_minimal_squad(), cb)
        d._spawn_agent("planner", "planner-1", None, [])
        assert started == ["planner-1"]

    def test_on_agent_done_called_after_completion(self, tmp_path):
        """on_agent_done receives the completed agent and its exit code."""
        done = []

        cb = _make_callbacks(tmp_path)
        cb.on_agent_done = lambda agent, rc: done.append((agent.agent_id, rc))

        d = Dispatcher(_minimal_squad(), cb)
        agent = _make_agent(tmp_path)
        d.pool.add(agent)
        d._handle_completion(agent, 0, [])
        assert done == [("coder-1", 0)]

    def test_on_agent_done_called_on_failure(self, tmp_path):
        """on_agent_done is called even when the agent exits non-zero."""
        done = []

        cb = _make_callbacks(tmp_path)
        cb.on_agent_done = lambda agent, rc: done.append((agent.agent_id, rc))

        d = Dispatcher(_minimal_squad(), cb)
        agent = _make_agent(tmp_path)
        d.pool.add(agent)
        d._handle_completion(agent, 1, [])
        assert done == [("coder-1", 1)]

    def test_on_agent_start_none_is_safe(self, tmp_path):
        """on_agent_start=None (default) does not crash."""

        def _spawn(ctx, cwd, model, log):
            return FakePopen(), None

        cb = _make_callbacks(tmp_path, spawn_fn=_spawn)
        assert cb.on_agent_start is None

        d = Dispatcher(_minimal_squad(), cb)
        d._spawn_agent("planner", "planner-1", None, [])  # must not raise

    def test_on_agent_done_none_is_safe(self, tmp_path):
        """on_agent_done=None (default) does not crash."""
        cb = _make_callbacks(tmp_path)
        assert cb.on_agent_done is None

        d = Dispatcher(_minimal_squad(), cb)
        agent = _make_agent(tmp_path)
        d.pool.add(agent)
        d._handle_completion(agent, 0, [])  # must not raise


class TestDispatcherLoopProperty:
    def test_loop_starts_at_zero(self, tmp_path):
        cb = _make_callbacks(tmp_path)
        d = Dispatcher(_minimal_squad(), cb)
        assert d.loop == 0

    def test_loop_increments_each_cycle(self, tmp_path, monkeypatch):
        """loop property reflects the number of _loop iterations run."""
        import orc.dispatcher as _d

        monkeypatch.setattr(_d, "_POLL_INTERVAL", 0)

        cb = _make_callbacks(
            tmp_path,
            get_open_tasks=lambda: [],
            get_messages=lambda: [],
        )
        d = Dispatcher(_minimal_squad(), cb)
        # Run one maxloops cycle (spawns planner then stops when pool drains).
        d.run(maxloops=1)
        assert d.loop >= 1
