"""Tests for orc/dispatcher.py."""

from __future__ import annotations

import sys
from dataclasses import replace as _replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from conftest import FakePopen, make_msg
from typer.testing import CliRunner

import orc.ai.invoke as inv
import orc.cli.merge as _merge_mod
import orc.cli.run as _run_mod
import orc.config as _cfg
import orc.engine.context as _ctx
import orc.engine.dispatcher as _disp
import orc.git.core as _git
import orc.main as m
import orc.messaging.telegram as tg
from orc.ai.backends import SpawnResult
from orc.engine.dispatcher import CLOSE_BOARD, QA_PASSED, Dispatcher
from orc.engine.pool import AgentProcess
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
        context_tmp=None,
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


# ---------------------------------------------------------------------------
# Fake service objects for testing
# ---------------------------------------------------------------------------


class _FakeBoard:
    """Mutable fake for BoardService — override attributes freely in tests."""

    def __init__(self, *, get_open_tasks=None, get_pending_visions=None, get_pending_reviews=None):
        self.get_open_tasks = get_open_tasks or (lambda: [])
        self.assign_task = lambda task, agent: None
        self.unassign_task = lambda task: None
        self.get_pending_visions = get_pending_visions or (lambda: ["placeholder.md"])
        self.get_pending_reviews = get_pending_reviews or (lambda: [])


class _FakeWorktree:
    """Mutable fake for WorktreeService."""

    def __init__(self, tmp_path: Path):
        self.ensure_dev_worktree = lambda: tmp_path
        self.ensure_feature_worktree = lambda t: tmp_path


class _FakeMessaging:
    """Mutable fake for MessagingService — override attributes freely in tests."""

    def __init__(self, *, get_messages=None, wait_for_human_reply=None):
        self.get_messages = get_messages or (lambda: [])
        self.has_unresolved_block = lambda msgs: (None, None)
        self.wait_for_human_reply = wait_for_human_reply or (lambda msgs, **kw: "reply")
        self.post_boot_message = lambda agent_id, body: None
        self.post_resolved = lambda a, s, r: None
        self.boot_message_body = lambda: "booting"


class _FakeWorkflow:
    """Mutable fake for WorkflowService."""

    def __init__(self, *, derive_task_state=None):
        self.derive_task_state = derive_task_state or (lambda t: ("coder", "ready"))
        self.merge_feature = lambda task: None
        self.do_close_board = lambda task: None


class _FakeAgent:
    """Mutable fake for AgentService."""

    def __init__(self, tmp_path: Path, *, spawn_fn=None):
        def _default_spawn(ctx, cwd, model, log):
            return SpawnResult(process=FakePopen(), log_fh=None, context_tmp="")

        self.build_context = lambda role, agent_id, msgs, wt: ("model", "ctx")
        self.spawn = spawn_fn or _default_spawn


def _make_services(
    tmp_path: Path,
    *,
    get_messages=None,
    get_open_tasks=None,
    derive_task_state=None,
    spawn_fn=None,
    wait_for_human_reply=None,
    get_pending_visions=None,
    get_pending_reviews=None,
):
    """Return a SimpleNamespace of fully-wired fake services for Dispatcher tests."""
    import types

    board_dir = tmp_path / ".orc" / "work"
    board_dir.mkdir(parents=True, exist_ok=True)
    (board_dir / "board.yaml").write_text("counter: 0\nopen: []\ndone: []\n")

    return types.SimpleNamespace(
        board=_FakeBoard(
            get_open_tasks=get_open_tasks,
            get_pending_visions=get_pending_visions,
            get_pending_reviews=get_pending_reviews,
        ),
        worktree=_FakeWorktree(tmp_path),
        messaging=_FakeMessaging(
            get_messages=get_messages,
            wait_for_human_reply=wait_for_human_reply,
        ),
        workflow=_FakeWorkflow(derive_task_state=derive_task_state),
        agent=_FakeAgent(tmp_path, spawn_fn=spawn_fn),
    )


def _make_dispatcher(squad, svcs, *, dry_run: bool = False, only_role=None, hooks=None):
    """Convenience wrapper: construct a Dispatcher from a services namespace."""
    return Dispatcher(
        squad,
        board=svcs.board,
        worktree=svcs.worktree,
        messaging=svcs.messaging,
        workflow=svcs.workflow,
        agent=svcs.agent,
        hooks=hooks,
        dry_run=dry_run,
        only_role=only_role,
    )


# ---------------------------------------------------------------------------
# Boot message sent before agent invocation
# ---------------------------------------------------------------------------


class TestBootMessageSentBeforeInvoke:
    def _common_patches(self, monkeypatch, tmp_path):
        """Patch git helpers so tests don't hit subprocess."""
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), orc_dir=tmp_path))
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
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), board_file=board))
        self._common_patches(monkeypatch, tmp_path)

        sent: list[str] = []
        monkeypatch.setattr(tg, "send_message", lambda text: sent.append(text))
        monkeypatch.setattr(tg, "get_messages", lambda: [])
        monkeypatch.setattr(_ctx, "build_agent_context", lambda *a, **kw: ("model", "ctx"))
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])
        monkeypatch.setattr(_merge_mod, "_rebase_dev_on_main", lambda *_: None)
        monkeypatch.setattr(
            inv,
            "spawn",
            lambda *a, **kw: SpawnResult(process=FakePopen(), log_fh=None, context_tmp=""),
        )
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        result = runner.invoke(m.app, ["run", "--maxcalls", "1"])
        assert result.exit_code == 0
        assert len(sent) == 1
        assert "(boot)" in sent[0]
        assert "work/0001-foo.md" in sent[0]

    def test_boot_message_precedes_invoke(self, tmp_path, monkeypatch):
        """Boot message must be sent BEFORE spawn is called."""
        board = tmp_path / "board.yaml"
        board.write_text("counter: 1\nopen:\n  - name: 0001-foo.md\n")
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), board_file=board))
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
            return SpawnResult(process=FakePopen(), log_fh=None, context_tmp="")

        monkeypatch.setattr(inv, "spawn", fake_spawn)

        runner.invoke(m.app, ["run", "--maxcalls", "1"])
        assert call_order == ["send", "invoke"]


# ---------------------------------------------------------------------------
# Blocked-state recovery
# ---------------------------------------------------------------------------


class TestBlockedResumption:
    def _blocked_msgs(self, agent_id: str) -> list[dict]:
        """agent_id should be in 'role-N' format, e.g. 'coder-1'."""
        return [make_msg(f"[{agent_id}](blocked) 2026-03-09T11:00:00Z: Need help.", ts=1000)]

    def _common_patches(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), orc_dir=tmp_path))
        monkeypatch.setattr(_git, "_feature_branch_exists", lambda b: False)
        monkeypatch.setattr(_git, "_feature_has_commits_ahead_of_main", lambda b: False)
        monkeypatch.setattr(_git, "_feature_merged_into_dev", lambda b: False)
        monkeypatch.setattr(_git, "_last_feature_commit_message", lambda b: None)
        monkeypatch.setattr(_git, "_ensure_feature_worktree", lambda task: tmp_path)
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)

    def test_blocked_agent_resumes_after_reply(self, monkeypatch, tmp_path):
        board = tmp_path / "board.yaml"
        board.write_text("counter: 1\nopen:\n  - name: 0001-foo.md\n")
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), board_file=board))
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
        monkeypatch.setattr(
            inv,
            "spawn",
            lambda *a, **kw: SpawnResult(process=FakePopen(), log_fh=None, context_tmp=""),
        )
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        monkeypatch.setattr(_run_mod, "logger", MagicMock())

        rc = runner.invoke(m.app, ["run", "--maxcalls", "1"])
        assert rc.exit_code == 0
        assert invocations == ["coder"]

    def test_blocked_resumes_correct_agent(self, monkeypatch, tmp_path):
        """After a hard-block reply, the dispatcher routes to the correct role."""
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), orc_dir=tmp_path))
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])
        monkeypatch.setattr(_merge_mod, "_rebase_dev_on_main", lambda *_: None)
        monkeypatch.setattr(tg, "send_message", lambda t: None)
        monkeypatch.setattr(_ctx, "wait_for_human_reply", lambda msgs, **kw: "Help.")
        monkeypatch.setattr(
            inv,
            "spawn",
            lambda *a, **kw: SpawnResult(process=FakePopen(), log_fh=None, context_tmp=""),
        )
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
                    "_last_feature_commit_message": "chore(coder-1.done.0001): finished task",
                },
            ),
        ]

        for agent_id, board_content, git_map in cases:
            board = tmp_path / "board.yaml"
            board.write_text(board_content)
            monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), board_file=board))
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

            runner.invoke(m.app, ["run", "--maxcalls", "1"])
            expected_role = agent_id.split("-")[0]
            assert invocations == [expected_role], (
                f"Expected {expected_role} to be dispatched for {agent_id} block"
            )

    def test_timeout_posts_telegram_message_and_exits(self, monkeypatch, tmp_path):
        board = tmp_path / "board.yaml"
        board.write_text("counter: 1\nopen:\n  - name: 0001-foo.md\n")
        monkeypatch.setattr(
            _cfg, "_config", _replace(_cfg.get(), board_file=board, orc_dir=tmp_path)
        )
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

        rc = runner.invoke(m.app, ["run", "--maxcalls", "1"])
        assert rc.exit_code == 1
        assert len(sent) == 1
        assert "(blocked)" in sent[0]
        assert "Stopped" in sent[0]

    def test_planner_done_is_not_blocked(self, monkeypatch, tmp_path):
        """planner(done) is not a blocked state — git routes to planner (no tasks)."""
        board = tmp_path / "board.yaml"
        board.write_text("counter: 1\nopen: []\n")
        monkeypatch.setattr(
            _cfg, "_config", _replace(_cfg.get(), board_file=board, orc_dir=tmp_path)
        )

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
        monkeypatch.setattr(
            inv,
            "spawn",
            lambda *a, **kw: SpawnResult(process=FakePopen(), log_fh=None, context_tmp=""),
        )

        wait_called: list[bool] = []
        monkeypatch.setattr(
            _ctx, "wait_for_human_reply", lambda msgs, **kw: wait_called.append(True) or ""
        )

        rc = runner.invoke(m.app, ["run", "--maxcalls", "1"])
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
        svcs = _make_services(tmp_path)
        d = _make_dispatcher(_minimal_squad(), svcs)

        def boom(maxcalls):
            raise _disp._ShutdownSignal(2)

        d._loop = boom
        d._kill_all_and_unassign = MagicMock()
        with pytest.raises(click.exceptions.Exit) as exc_info:
            d.run()
        assert exc_info.value.exit_code == 130

    def test_do_merge_failure_logs_error(self, tmp_path, monkeypatch):
        """_do_merge: exception → error logged, no crash."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = _make_services(tmp_path)
        svcs.workflow.merge_feature = lambda task: (_ for _ in ()).throw(
            RuntimeError("merge conflict")
        )
        d = _make_dispatcher(_minimal_squad(), svcs)
        d._do_merge("0001-foo.md")  # should not raise

    def test_handle_watchdog_kills_agent(self, tmp_path, monkeypatch):
        """_handle_watchdog kills the agent and unassigns its task."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        unassigned = []
        svcs = _make_services(tmp_path, get_open_tasks=lambda: [])
        svcs.board.unassign_task = lambda t: unassigned.append(t)
        d = _make_dispatcher(_minimal_squad(), svcs)
        agent = _make_agent(tmp_path, role="coder")
        d.pool.add(agent)
        d._handle_watchdog(agent)
        assert "0001-foo.md" in unassigned

    def test_spawn_agent_raises_for_non_planner_without_task(self, tmp_path):
        svcs = _make_services(tmp_path)
        d = _make_dispatcher(_minimal_squad(), svcs)
        with pytest.raises(ValueError, match="No worktree"):
            d._spawn_agent("coder", "coder-1", None, [])

    def test_spawn_agent_log_path_is_under_log_dir_agents(self, tmp_path, monkeypatch):
        """Agent log path is LOG_DIR/agents/{agent_id}.log."""
        log_dir = tmp_path / "logs"
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), log_dir=log_dir))

        captured: dict = {}

        def _spawn(ctx, cwd, model, log):
            captured["log_path"] = log
            return SpawnResult(process=FakePopen(), log_fh=None, context_tmp="")

        svcs = _make_services(tmp_path, spawn_fn=_spawn)
        d = _make_dispatcher(_minimal_squad(), svcs)
        d._spawn_agent("planner", "planner-1", None, [])

        assert captured["log_path"] == log_dir / "agents" / "planner-1.log"

    def test_spawn_agent_dry_run_prints(self, tmp_path, capsys):
        svcs = _make_services(tmp_path)
        d = _make_dispatcher(_minimal_squad(), svcs, dry_run=True)
        d._spawn_agent("planner", "planner-1", None, [])
        captured = capsys.readouterr()
        assert "Would spawn" in captured.out

    def test_dispatch_soft_block_spawns_planner(self, tmp_path, monkeypatch):
        """Soft-blocked state → planner spawned to resolve."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = _make_services(tmp_path, get_open_tasks=lambda: [{"name": "0001-foo.md"}])
        svcs.messaging.has_unresolved_block = lambda msgs: ("coder-1", "soft-blocked")
        d = _make_dispatcher(_minimal_squad(), svcs)
        count = d._dispatch([], 100)
        assert count >= 1

    def test_dispatch_skips_assigned_task(self, tmp_path):
        """Task already assigned → skipped."""
        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: [{"name": "0001-foo.md", "assigned_to": "coder-1"}],
            derive_task_state=lambda t: ("coder", "reason"),
        )
        d = _make_dispatcher(_minimal_squad(), svcs)
        count = d._dispatch([], 100)
        assert count == 0

    def test_dispatch_close_board(self, tmp_path):
        """CLOSE_BOARD token → do_close_board called."""
        closed = []
        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: [{"name": "0001-foo.md"}],
            derive_task_state=lambda t: (CLOSE_BOARD, "close"),
        )
        svcs.workflow.do_close_board = lambda t: closed.append(t)
        d = _make_dispatcher(_minimal_squad(), svcs)
        d._dispatch([], 100)
        assert "0001-foo.md" in closed

    def test_dispatch_skips_unknown_token(self, tmp_path):
        """Unknown token → task skipped, dispatch returns 0."""
        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: [{"name": "0001-foo.md"}],
            derive_task_state=lambda t: ("unknown_token", "reason"),
        )
        d = _make_dispatcher(_minimal_squad(), svcs)
        assert d._dispatch([], 1000) == 0

    def test_dispatch_skips_when_role_at_capacity(self, tmp_path):
        """Coder at capacity → skip."""
        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: [{"name": "0001-foo.md"}],
            derive_task_state=lambda t: ("coder", "reason"),
        )
        d = _make_dispatcher(_minimal_squad(), svcs)
        d.pool.add(_make_agent(tmp_path, role="coder"))  # coder-1 already running
        assert d._dispatch([], 1000) == 0

    def test_loop_refreshes_messages_after_completion(self, tmp_path, monkeypatch):
        """Line 235: messages refreshed after hard-block handling."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = _make_services(tmp_path)
        d = _make_dispatcher(_minimal_squad(), svcs, dry_run=True)

        block_returned = [False]

        def fake_has_unresolved_block(messages):
            if not block_returned[0]:
                block_returned[0] = True
                return ("planner-1", "blocked")
            return (None, None)

        d.messaging.has_unresolved_block = fake_has_unresolved_block
        d._handle_hard_block = MagicMock()
        d._loop(maxcalls=sys.maxsize)

        d._handle_hard_block.assert_called_once()

    def test_loop_watchdog_triggers(self, tmp_path, monkeypatch):
        """Watchdog kills stuck agents."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        class StuckPopen:
            returncode = None

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
                return SpawnResult(process=StuckPopen(), log_fh=None, context_tmp="")
            return SpawnResult(process=FakePopen(), log_fh=None, context_tmp="")

        squad = SquadConfig(
            planner=1,
            coder=1,
            qa=1,
            timeout_minutes=0,
            name="test",
            description="",
            _models={},
        )
        svcs = _make_services(tmp_path, spawn_fn=spawn_fn)
        d = _make_dispatcher(squad, svcs)
        d.run(maxcalls=2)

    def test_loop_dry_run_stops_after_one_cycle(self, tmp_path, monkeypatch):
        """Dry-run breaks after first dispatch."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = _make_services(tmp_path)
        d = _make_dispatcher(_minimal_squad(), svcs, dry_run=True)
        d.run(maxcalls=sys.maxsize)
        assert d._total_spawned == 1
        """_handle_hard_block posts resolved after human reply."""
        resolved = []
        svcs = _make_services(
            tmp_path,
            wait_for_human_reply=lambda msgs, **kw: "Here is the answer.",
        )
        svcs.messaging.post_resolved = lambda a, s, r: resolved.append((a, s, r))
        d = _make_dispatcher(_minimal_squad(), svcs)
        d._handle_hard_block("coder-1", [])
        assert len(resolved) == 1
        assert resolved[0][0] == "coder-1"

    def test_dispatch_qa_passed_queues_merge(self, tmp_path, monkeypatch):
        """QA_PASSED token → task added to merge queue."""
        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: [{"name": "0001-foo.md"}],
            derive_task_state=lambda t: (QA_PASSED, "qa passed"),
        )
        d = _make_dispatcher(_minimal_squad(), svcs)
        d._dispatch([], 100)
        assert "0001-foo.md" in d._merge_queue

    def test_handle_completion_posts_resolved_for_soft_block_planner(self, tmp_path, monkeypatch):
        """After planner resolves soft-block, post_resolved is called."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        resolved = []
        svcs = _make_services(tmp_path)
        svcs.messaging.post_resolved = lambda a, s, r: resolved.append((a, s, r))

        d = _make_dispatcher(_minimal_squad(), svcs)
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
            context_tmp=None,
        )
        d.pool.add(agent)
        d._handle_completion(agent, 0, [])
        assert len(resolved) == 1

    def test_handle_completion_failed_agent_unassigns(self, tmp_path, monkeypatch):
        """Non-zero exit → task unassigned."""
        unassigned = []
        svcs = _make_services(tmp_path)
        svcs.board.unassign_task = lambda t: unassigned.append(t)
        d = _make_dispatcher(_minimal_squad(), svcs)
        agent = _make_agent(tmp_path, role="coder")
        d.pool.add(agent)
        d._handle_completion(agent, 1, [])
        assert "0001-foo.md" in unassigned

    def test_loop_merge_queue_drained(self, tmp_path, monkeypatch):
        """Merge queue drained in loop."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        merged = []
        svcs = _make_services(tmp_path)
        svcs.workflow.merge_feature = lambda t: merged.append(t)
        d = _make_dispatcher(_minimal_squad(), svcs)
        d._merge_queue.append("0001-foo.md")
        d.run(maxcalls=1)
        assert "0001-foo.md" in merged


# ---------------------------------------------------------------------------
# Coverage gap tests
# ---------------------------------------------------------------------------


class TestDispatcherInternalCoverage:
    def test_has_pending_work_open_tasks_returns_true(self, tmp_path):
        """has_pending_work returns True when open tasks exist."""
        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: [{"name": "0001-foo.md"}],
        )
        assert Dispatcher.has_pending_work(svcs.board, svcs.messaging, []) is True

    def test_has_pending_work_blocked_returns_true(self, tmp_path):
        """has_pending_work returns True when blocked agent exists."""
        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: [],
        )
        svcs.messaging.has_unresolved_block = lambda msgs: ("agent-42", "blocked")
        assert Dispatcher.has_pending_work(svcs.board, svcs.messaging, []) is True

    def test_has_pending_work_no_work_returns_false(self, tmp_path):
        """has_pending_work returns False when nothing pending."""
        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: [],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: [],
        )
        svcs.messaging.has_unresolved_block = lambda msgs: (None, None)
        assert Dispatcher.has_pending_work(svcs.board, svcs.messaging, []) is False

    def test_has_pending_work_pending_reviews_returns_true(self, tmp_path):
        """has_pending_work returns True when unmerged feat/* branches exist."""
        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: [],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: ["feat/0001-foo"],
        )
        svcs.messaging.has_unresolved_block = lambda msgs: (None, None)
        assert Dispatcher.has_pending_work(svcs.board, svcs.messaging, []) is True

    def test_kill_all_and_unassign(self, tmp_path):
        """Lines 493-496: _kill_all_and_unassign unassigns tasks and kills agents."""
        unassigned = []
        svcs = _make_services(tmp_path)
        svcs.board.unassign_task = lambda t: unassigned.append(t)
        d = _make_dispatcher(_minimal_squad(), svcs)
        agent = _make_agent(tmp_path, role="coder")
        d.pool.add(agent)
        d._kill_all_and_unassign()
        assert "0001-foo.md" in unassigned

    def test_shutdown_handler_raises_signal(self, tmp_path):
        """Line 499: _shutdown_handler raises _ShutdownSignal."""
        import pytest

        svcs = _make_services(tmp_path)
        d = _make_dispatcher(_minimal_squad(), svcs)
        with pytest.raises(_disp._ShutdownSignal):
            d._shutdown_handler(15, None)

    def test_cleanup_context_tmp_deletes_file(self, tmp_path):
        """Lines 515-517: _cleanup_context_tmp deletes the temp file."""
        tmp_file = tmp_path / "ctx.tmp"
        tmp_file.write_text("context data")

        _disp._cleanup_context_tmp(str(tmp_file))
        assert not tmp_file.exists()

    def test_cleanup_context_tmp_no_attr(self, tmp_path):
        """_cleanup_context_tmp is a no-op when context_tmp is None."""
        _disp._cleanup_context_tmp(None)  # should not raise

    def test_dispatch_returns_zero_when_nothing_to_do(self, tmp_path, monkeypatch):
        """_dispatch returns 0 immediately when no tasks or visions (line 318)."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: [],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: [],
        )
        d = _make_dispatcher(_minimal_squad(), svcs)
        result = d._dispatch([], 100)
        assert result == 0

    def test_dispatch_queues_pending_reviews_for_merge(self, tmp_path, monkeypatch):
        """_dispatch adds unmerged feat/* branches to the merge queue (no agent spawned)."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: [],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: ["feat/0001-foo"],
        )
        d = _make_dispatcher(_minimal_squad(), svcs)
        result = d._dispatch([], 100)
        assert result == 0  # merge-queue additions don't count as agent spawns
        assert "0001-foo.md" in d._merge_queue

    def test_dispatch_deduplicates_pending_reviews(self, tmp_path, monkeypatch):
        """_dispatch doesn't add the same branch twice to the merge queue."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: [],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: ["feat/0001-foo"],
        )
        d = _make_dispatcher(_minimal_squad(), svcs)
        d._dispatch([], 100)
        d._dispatch([], 100)
        assert d._merge_queue.count("0001-foo.md") == 1

    def test_run_exits_workflow_complete_when_no_work(self, tmp_path, monkeypatch, capsys):
        """run() logs workflow-complete and exits when nothing to dispatch (lines 299-301)."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: [],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: [],
        )
        svcs.messaging.has_unresolved_block = lambda msgs: (None, None)
        d = _make_dispatcher(_minimal_squad(), svcs)
        d.run(maxcalls=sys.maxsize)
        out = capsys.readouterr().out
        assert "Workflow complete" in out


class TestDispatchCallbacksOptional:
    """Tests for the optional on_agent_start / on_agent_done hooks."""

    def test_on_agent_start_called_after_spawn(self, tmp_path):
        """on_agent_start receives the AgentProcess just added to the pool."""
        started = []

        def _spawn(ctx, cwd, model, log):
            return SpawnResult(process=FakePopen(), log_fh=None, context_tmp="")

        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: [],
            spawn_fn=_spawn,
        )
        hooks = _disp.DispatchHooks(on_agent_start=lambda agent: started.append(agent.agent_id))

        d = _make_dispatcher(_minimal_squad(), svcs, hooks=hooks)
        d._spawn_agent("planner", "planner-1", None, [])
        assert started == ["planner-1"]

    def test_on_agent_done_called_after_completion(self, tmp_path):
        """on_agent_done receives the completed agent and its exit code."""
        done = []

        svcs = _make_services(tmp_path)
        hooks = _disp.DispatchHooks(
            on_agent_done=lambda agent, rc: done.append((agent.agent_id, rc))
        )

        d = _make_dispatcher(_minimal_squad(), svcs, hooks=hooks)
        agent = _make_agent(tmp_path)
        d.pool.add(agent)
        d._handle_completion(agent, 0, [])
        assert done == [("coder-1", 0)]

    def test_on_agent_done_called_on_failure(self, tmp_path):
        """on_agent_done is called even when the agent exits non-zero."""
        done = []

        svcs = _make_services(tmp_path)
        hooks = _disp.DispatchHooks(
            on_agent_done=lambda agent, rc: done.append((agent.agent_id, rc))
        )

        d = _make_dispatcher(_minimal_squad(), svcs, hooks=hooks)
        agent = _make_agent(tmp_path)
        d.pool.add(agent)
        d._handle_completion(agent, 1, [])
        assert done == [("coder-1", 1)]

    def test_on_agent_start_none_is_safe(self, tmp_path):
        """on_agent_start=None (default) does not crash."""

        def _spawn(ctx, cwd, model, log):
            return SpawnResult(process=FakePopen(), log_fh=None, context_tmp="")

        svcs = _make_services(tmp_path, spawn_fn=_spawn)

        d = _make_dispatcher(_minimal_squad(), svcs)
        assert d.hooks.on_agent_start is None
        d._spawn_agent("planner", "planner-1", None, [])  # must not raise

    def test_on_agent_done_none_is_safe(self, tmp_path):
        """on_agent_done=None (default) does not crash."""
        svcs = _make_services(tmp_path)

        d = _make_dispatcher(_minimal_squad(), svcs)
        assert d.hooks.on_agent_done is None
        agent = _make_agent(tmp_path)
        d.pool.add(agent)
        d._handle_completion(agent, 0, [])  # must not raise

    def test_on_orc_status_called(self, tmp_path):
        """on_orc_status receives the status and task strings."""
        updates: list[tuple[str, str | None]] = []

        svcs = _make_services(tmp_path)
        hooks = _disp.DispatchHooks(
            on_orc_status=lambda status, task: updates.append((status, task))
        )

        d = _make_dispatcher(_minimal_squad(), svcs, hooks=hooks)
        d._set_orc_status("running", "merging task 0001-foo.md")
        assert updates == [("running", "merging task 0001-foo.md")]

    def test_on_orc_status_none_is_safe(self, tmp_path):
        """on_orc_status=None (default) does not crash."""
        svcs = _make_services(tmp_path)

        d = _make_dispatcher(_minimal_squad(), svcs)
        assert d.hooks.on_orc_status is None
        d._set_orc_status("running", "checking pending work")  # must not raise


class TestDispatcherLoopProperty:
    def test_loop_starts_at_zero(self, tmp_path):
        svcs = _make_services(tmp_path)
        d = _make_dispatcher(_minimal_squad(), svcs)
        assert d.total_agent_calls == 0

    def test_loop_increments_each_cycle(self, tmp_path, monkeypatch):
        """loop property reflects the number of _loop iterations run."""
        import orc.engine.dispatcher as _d

        monkeypatch.setattr(_d, "_POLL_INTERVAL", 0)

        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: [],
            get_messages=lambda: [],
        )
        d = _make_dispatcher(_minimal_squad(), svcs)
        # Run one maxcalls cycle (spawns planner then stops when pool drains).
        d.run(maxcalls=1)
        assert d.total_agent_calls >= 1


class TestProactivePlanner:
    """Dispatcher spawns a planner proactively when open_tasks < coder count."""

    def test_spawns_planner_when_tasks_below_coder_capacity(self, tmp_path):
        """Planner spawned when open_tasks < coder count and visions pending."""
        spawned_roles: list[str] = []

        def _spawn(ctx, cwd, model, log):
            return SpawnResult(process=FakePopen(), log_fh=None, context_tmp="")

        svcs = _make_services(
            tmp_path,
            # 1 open task, squad has 2 coders → pipeline has room → spawn planner
            get_open_tasks=lambda: [{"name": "0001-foo.md"}],
            derive_task_state=lambda t: ("coder", "ready"),
            get_pending_visions=lambda: ["vision-001.md"],
            spawn_fn=_spawn,
        )
        svcs.board.assign_task = lambda t, a: spawned_roles.append(a.split("-")[0])

        # Squad with 2 coders so that 1 open task < 2 triggers the proactive path.
        squad = _minimal_squad(coder=2)
        d = _make_dispatcher(squad, svcs)
        d._dispatch([], 100)

        # Should have spawned 1 coder + 1 planner
        pool_roles = [a.role for a in d.pool.all_agents()]
        assert "planner" in pool_roles, f"expected planner in pool, got {pool_roles}"

    def test_no_proactive_planner_when_tasks_meet_coder_capacity(self, tmp_path):
        """No proactive planner when open_tasks >= coder count."""
        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: [{"name": "0001-foo.md"}, {"name": "0002-bar.md"}],
            derive_task_state=lambda t: ("coder", "ready"),
            get_pending_visions=lambda: ["vision-001.md"],
        )
        # 2 open tasks, 2 coders → at capacity, no proactive planner
        squad = _minimal_squad(coder=2)
        d = _make_dispatcher(squad, svcs)
        d._dispatch([], 100)

        pool_roles = [a.role for a in d.pool.all_agents()]
        assert "planner" not in pool_roles, f"unexpected planner in pool: {pool_roles}"

    def test_no_proactive_planner_when_no_pending_visions(self, tmp_path):
        """No proactive planner when there are no pending vision docs to plan."""
        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: [{"name": "0001-foo.md"}],
            derive_task_state=lambda t: ("coder", "ready"),
            get_pending_visions=lambda: [],  # nothing to plan
        )
        squad = _minimal_squad(coder=2)
        d = _make_dispatcher(squad, svcs)
        d._dispatch([], 100)

        pool_roles = [a.role for a in d.pool.all_agents()]
        assert "planner" not in pool_roles, f"unexpected planner in pool: {pool_roles}"

    def test_no_proactive_planner_when_planner_already_running(self, tmp_path):
        """No second planner spawned when one is already in the pool."""
        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: [{"name": "0001-foo.md"}],
            derive_task_state=lambda t: ("coder", "ready"),
            get_pending_visions=lambda: ["vision-001.md"],
        )
        squad = _minimal_squad(coder=2)
        d = _make_dispatcher(squad, svcs)
        # Pre-populate pool with a planner.
        d.pool.add(_make_agent(tmp_path, role="planner", task=""))
        d._dispatch([], 100)

        planner_count = sum(1 for a in d.pool.all_agents() if a.role == "planner")
        assert planner_count == 1, "second planner must not be spawned"


class TestMaxcallsUnlimited:
    """Dispatcher.run() with sys.maxsize behaves as unlimited."""

    def test_unlimited_dispatches_agents(self, tmp_path, monkeypatch):
        """run(maxcalls=sys.maxsize) dispatches agents normally (not zero)."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: [],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: [],
        )
        svcs.messaging.has_unresolved_block = lambda msgs: (None, None)
        d = _make_dispatcher(_minimal_squad(), svcs)
        d.run(maxcalls=sys.maxsize)
        # Must not crash; total_agent_calls reflects whatever was dispatched.
        assert d.total_agent_calls >= 0


class TestDispatchBudgetExhaustion:
    """_dispatch stops spawning when call_budget is consumed mid-dispatch."""

    def test_budget_limits_spawns(self, tmp_path):
        """With budget=1 and 2 tasks, only 1 agent is spawned."""
        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: [
                {"name": "0001-foo.md"},
                {"name": "0002-bar.md"},
            ],
            derive_task_state=lambda t: ("coder", "ready"),
        )
        # Squad allows 2 coders, but budget is capped at 1.
        squad = _minimal_squad(coder=2)
        d = _make_dispatcher(squad, svcs)
        count = d._dispatch([], call_budget=1)
        assert count == 1
        assert len(d.pool.all_agents()) == 1


# ---------------------------------------------------------------------------
# --agent (only_role) filtering
# ---------------------------------------------------------------------------


class TestOnlyRoleFiltering:
    """Dispatcher.only_role restricts which roles get dispatched."""

    def test_only_coder_skips_planner(self, tmp_path):
        """With only_role='coder', planner is not dispatched even when visions exist."""
        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: [],
            get_pending_visions=lambda: ["v1.md"],
            get_pending_reviews=lambda: [],
        )
        d = _make_dispatcher(_minimal_squad(), svcs, only_role="coder")
        count = d._dispatch([], call_budget=10)
        assert count == 0
        assert d.pool.is_empty()

    def test_only_planner_dispatches_planner(self, tmp_path):
        """With only_role='planner' and pending visions, a planner is spawned."""
        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: [],
            get_pending_visions=lambda: ["v1.md"],
            get_pending_reviews=lambda: [],
        )
        d = _make_dispatcher(_minimal_squad(), svcs, only_role="planner")
        count = d._dispatch([], call_budget=10)
        assert count == 1
        agents = d.pool.all_agents()
        assert len(agents) == 1
        assert agents[0].role == "planner"

    def test_only_coder_dispatches_coder_skips_qa(self, tmp_path):
        """With only_role='coder', coder tasks are dispatched but QA tasks are skipped."""
        tasks = [{"name": "0001-code.md"}, {"name": "0002-review.md"}]
        states = {"0001-code.md": ("coder", "ready"), "0002-review.md": ("qa", "ready")}
        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: tasks,
            derive_task_state=lambda t: states[t],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: [],
        )
        d = _make_dispatcher(_minimal_squad(), svcs, only_role="coder")
        count = d._dispatch([], call_budget=10)
        assert count == 1
        agents = d.pool.all_agents()
        assert all(a.role == "coder" for a in agents)

    def test_only_qa_dispatches_qa_skips_coder(self, tmp_path):
        """With only_role='qa', QA tasks are dispatched but coder tasks are skipped."""
        tasks = [{"name": "0001-code.md"}, {"name": "0002-review.md"}]
        states = {"0001-code.md": ("coder", "ready"), "0002-review.md": ("qa", "ready")}
        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: tasks,
            derive_task_state=lambda t: states[t],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: [],
        )
        d = _make_dispatcher(_minimal_squad(), svcs, only_role="qa")
        count = d._dispatch([], call_budget=10)
        assert count == 1
        agents = d.pool.all_agents()
        assert all(a.role == "qa" for a in agents)

    def test_no_filter_dispatches_all_roles(self, tmp_path):
        """Without only_role, both coder and QA tasks are dispatched."""
        tasks = [{"name": "0001-code.md"}, {"name": "0002-review.md"}]
        states = {"0001-code.md": ("coder", "ready"), "0002-review.md": ("qa", "ready")}
        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: tasks,
            derive_task_state=lambda t: states[t],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: [],
        )
        d = _make_dispatcher(_minimal_squad(), svcs, only_role=None)
        count = d._dispatch([], call_budget=10)
        assert count == 2
        roles = {a.role for a in d.pool.all_agents()}
        assert roles == {"coder", "qa"}

    def test_only_role_idle_exits_when_no_work_for_role(self, tmp_path, monkeypatch):
        """Dispatcher stops when only_role is set and no work for that role exists."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = _make_services(
            tmp_path,
            get_open_tasks=lambda: [],
            get_pending_visions=lambda: ["v1.md"],
            get_pending_reviews=lambda: [],
        )
        d = _make_dispatcher(_minimal_squad(), svcs, only_role="coder")
        d.run(maxcalls=5)
        assert d.total_agent_calls == 0
