"""Tests for orc/main.py – boot message, env validation, and blocked-state recovery."""

import time
from unittest.mock import MagicMock

import yaml as _yaml
from conftest import FakePopen, make_msg
from typer.testing import CliRunner

import orc.cli.merge as _merge_mod
import orc.config as _cfg
import orc.context as _ctx
import orc.dispatcher as _disp
import orc.git as _git
import orc.invoke as inv
import orc.main as m
import orc.telegram as tg

runner = CliRunner()

# ---------------------------------------------------------------------------
# _boot_message_body
# ---------------------------------------------------------------------------


class TestBootMessageBody:
    def test_single_open_task(self, tmp_path, monkeypatch):
        board = tmp_path / "board.yaml"
        board.write_text("counter: 2\nopen:\n  - name: 0002-foo.md\n")
        monkeypatch.setattr(_cfg, "BOARD_FILE", board)
        assert m._boot_message_body() == "picking up work/0002-foo.md."

    def test_multiple_open_tasks(self, tmp_path, monkeypatch):
        board = tmp_path / "board.yaml"
        board.write_text("counter: 3\nopen:\n  - name: 0002-foo.md\n  - name: 0003-bar.md\n")
        monkeypatch.setattr(_cfg, "BOARD_FILE", board)
        assert m._boot_message_body() == "picking up work/0002-foo.md, work/0003-bar.md."

    def test_no_open_tasks(self, tmp_path, monkeypatch):
        board = tmp_path / "board.yaml"
        board.write_text("counter: 2\nopen: []\n")
        monkeypatch.setattr(_cfg, "BOARD_FILE", board)
        assert m._boot_message_body() == "no open tasks on board."

    def test_missing_board(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_cfg, "BOARD_FILE", tmp_path / "nonexistent.yaml")
        assert m._boot_message_body() == "no open tasks on board."


# ---------------------------------------------------------------------------
# Boot message is sent before agent invocation
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
# wait_for_human_reply
# ---------------------------------------------------------------------------


class TestWaitForHumanReply:
    def _human(self, text: str, ts: int) -> dict:
        return {"text": text, "date": ts, "from": {"username": "pietro", "first_name": "Pietro"}}

    def test_returns_first_new_human_message(self, monkeypatch):
        snapshot = [make_msg("[coder-1](blocked) 2026-03-09T11:00:00Z: Need help.", ts=1000)]
        human = self._human("Here is the clarification.", ts=2000)
        monkeypatch.setattr(tg, "get_messages", lambda: snapshot + [human])
        times = iter([0.0, 1.0])
        monkeypatch.setattr(time, "monotonic", lambda: next(times))
        monkeypatch.setattr(time, "sleep", lambda s: None)

        result = m.wait_for_human_reply(snapshot, timeout=3600.0)
        assert result == "Here is the clarification."

    def test_skips_snapshot_messages(self, monkeypatch):
        old_human = self._human("old message", ts=500)
        snapshot = [old_human]
        new_human = self._human("new message", ts=600)
        monkeypatch.setattr(tg, "get_messages", lambda: snapshot + [new_human])
        times = iter([0.0, 1.0])
        monkeypatch.setattr(time, "monotonic", lambda: next(times))
        monkeypatch.setattr(time, "sleep", lambda s: None)

        result = m.wait_for_human_reply(snapshot, timeout=3600.0)
        assert result == "new message"

    def test_skips_agent_messages(self, monkeypatch):
        snapshot = [make_msg("[coder-1](blocked) 2026-03-09T11:00:00Z: Blocked.", ts=1000)]
        agent_msg = make_msg("[planner-1](ready) 2026-03-09T11:30:00Z: ADR updated.", ts=2000)
        human_msg = self._human("Please continue.", ts=3000)
        call_count = 0

        def get_messages():
            nonlocal call_count
            call_count += 1
            return snapshot + [agent_msg] if call_count == 1 else snapshot + [agent_msg, human_msg]

        monkeypatch.setattr(tg, "get_messages", get_messages)
        times = iter([0.0, 1.0, 2.0])
        monkeypatch.setattr(time, "monotonic", lambda: next(times))
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        result = m.wait_for_human_reply(snapshot, initial_delay=5.0, timeout=3600.0)
        assert result == "Please continue."
        assert len(sleeps) == 2

    def test_exponential_backoff(self, monkeypatch):
        snapshot: list[dict] = []
        human = self._human("Done.", ts=9000)
        call_count = 0

        def get_messages():
            nonlocal call_count
            call_count += 1
            return snapshot if call_count < 3 else [human]

        monkeypatch.setattr(tg, "get_messages", get_messages)
        times = iter([0.0, 1.0, 2.0, 3.0])
        monkeypatch.setattr(time, "monotonic", lambda: next(times))
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        m.wait_for_human_reply(
            snapshot, initial_delay=5.0, backoff_factor=2.0, max_delay=300.0, timeout=3600.0
        )
        assert sleeps == [5.0, 10.0, 20.0]

    def test_backoff_capped_at_max_delay(self, monkeypatch):
        snapshot: list[dict] = []
        human = self._human("Done.", ts=9000)
        call_count = 0

        def get_messages():
            nonlocal call_count
            call_count += 1
            return snapshot if call_count < 4 else [human]

        monkeypatch.setattr(tg, "get_messages", get_messages)
        times = iter([0.0, 1.0, 2.0, 3.0, 4.0])
        monkeypatch.setattr(time, "monotonic", lambda: next(times))
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        m.wait_for_human_reply(
            snapshot, initial_delay=5.0, backoff_factor=2.0, max_delay=10.0, timeout=3600.0
        )
        assert sleeps == [5.0, 10.0, 10.0, 10.0]

    def test_raises_timeout_error(self, monkeypatch):
        import pytest

        snapshot: list[dict] = []
        monkeypatch.setattr(tg, "get_messages", lambda: snapshot)
        times = iter([0.0, 3601.0])
        monkeypatch.setattr(time, "monotonic", lambda: next(times))
        monkeypatch.setattr(time, "sleep", lambda s: None)

        with pytest.raises(TimeoutError):
            m.wait_for_human_reply(snapshot, timeout=3600.0)

    def test_sleep_trimmed_to_deadline(self, monkeypatch):
        """Sleep must not overshoot the deadline."""
        import pytest

        snapshot: list[dict] = []
        monkeypatch.setattr(tg, "get_messages", lambda: snapshot)
        # deadline = 0 + 10 = 10; second monotonic = 9.0 → remaining = 1.0
        # sleep should be min(300, 1.0) = 1.0; third monotonic = 10.1 → TimeoutError
        times = iter([0.0, 9.0, 10.1])
        monkeypatch.setattr(time, "monotonic", lambda: next(times))
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        with pytest.raises(TimeoutError):
            m.wait_for_human_reply(snapshot, initial_delay=300.0, timeout=10.0)

        assert sleeps == [1.0]


# ---------------------------------------------------------------------------
# Blocked-state recovery in the main loop
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
        # Cycle 1: blocked → wait → post_resolved; then get_messages returns [] for cycle 2
        msg_iter = iter([blocked, []])
        monkeypatch.setattr(tg, "get_messages", lambda: next(msg_iter))

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

        # (agent_id, board_content, git_patches)
        cases = [
            (
                "planner-1",
                "counter: 1\nopen: []\n",
                {},  # empty board → planner dispatched
            ),
            (
                "coder-1",
                "counter: 1\nopen:\n  - name: 0001-foo.md\n",
                {  # open task, no feature branch → coder
                    "_feature_branch_exists": False,
                    "_feature_has_commits_ahead_of_main": False,
                    "_feature_merged_into_dev": False,
                    "_last_feature_commit_message": None,
                },
            ),
            (
                "qa-1",
                "counter: 1\nopen:\n  - name: 0001-foo.md\n",
                {  # feature branch with commits → qa
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
        # No open tasks → git routes to planner and runs it (no blocking)
        assert not wait_called
        assert invocations == ["planner"]


# ---------------------------------------------------------------------------
# merge command
# ---------------------------------------------------------------------------


class TestMergeCommand:
    def _setup(self, monkeypatch, tmp_path):
        board = tmp_path / "board.yaml"
        board.write_text("counter: 1\nopen:\n  - name: 0001-foo.md\n")
        monkeypatch.setattr(_cfg, "BOARD_FILE", board)
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path)
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])

    def test_clean_rebase_merges_and_returns(self, monkeypatch, tmp_path):
        """Clean rebase: _complete_merge is called, exit 0."""
        self._setup(monkeypatch, tmp_path)

        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)
        runs: list[list[str]] = []

        def fake_run(cmd, cwd=None, check=False, **kw):
            runs.append(cmd)
            r = MagicMock()
            r.returncode = 0
            return r

        monkeypatch.setattr(m.subprocess, "run", fake_run)
        completed: list[bool] = []
        monkeypatch.setattr(_git, "_complete_merge", lambda wt: completed.append(True))

        result = runner.invoke(m.app, ["merge"])
        assert result.exit_code == 0
        assert completed == [True]

    def test_conflict_delegates_to_coder_then_completes(self, monkeypatch, tmp_path):
        """On conflict the coder is invoked; after it finishes the merge completes."""
        self._setup(monkeypatch, tmp_path)
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)
        monkeypatch.setattr(tg, "get_messages", lambda: [])
        monkeypatch.setattr(tg, "send_message", lambda t: None)

        call_count = 0

        def fake_run(cmd, cwd=None, check=False, **kw):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            # Second call is `git rebase main` — simulate conflict
            r.returncode = 1 if cmd == ["git", "rebase", "main"] else 0
            r.stdout = "UU src/conflict.py\n"
            return r

        monkeypatch.setattr(m.subprocess, "run", fake_run)
        monkeypatch.setattr(_git, "_conflict_status", lambda wt: "UU src/conflict.py")
        monkeypatch.setattr(_git, "_rebase_in_progress", lambda wt: False)

        invocations: list[str] = []
        monkeypatch.setattr(
            _ctx, "invoke_agent", lambda name, ctx, mdl, **kw: invocations.append(name) or 0
        )
        monkeypatch.setattr(_ctx, "build_agent_context", lambda name, msgs, **kw: ("model", "ctx"))
        completed: list[bool] = []
        monkeypatch.setattr(_git, "_complete_merge", lambda wt: completed.append(True))

        result = runner.invoke(m.app, ["merge"])
        assert result.exit_code == 0
        assert invocations == ["coder"]
        assert "extra" in str(result.output).lower() or completed == [True]
        assert completed == [True]

    def test_conflict_agent_passes_conflict_extra_context(self, monkeypatch, tmp_path):
        """The coder agent receives an extra section describing the conflict."""
        self._setup(monkeypatch, tmp_path)
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)
        monkeypatch.setattr(tg, "get_messages", lambda: [])
        monkeypatch.setattr(tg, "send_message", lambda t: None)

        def fake_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 1 if cmd == ["git", "rebase", "main"] else 0
            r.stdout = ""
            return r

        monkeypatch.setattr(m.subprocess, "run", fake_run)
        monkeypatch.setattr(_git, "_conflict_status", lambda wt: "UU src/foo.py")
        monkeypatch.setattr(_git, "_rebase_in_progress", lambda wt: False)
        monkeypatch.setattr(_ctx, "invoke_agent", lambda name, ctx, mdl, **kw: 0)
        monkeypatch.setattr(_git, "_complete_merge", lambda wt: None)

        received_extra: list[str] = []

        def capture_context(name, msgs, extra="", **kwargs):
            received_extra.append(extra)
            return "model", "ctx"

        monkeypatch.setattr(_ctx, "build_agent_context", capture_context)

        runner.invoke(m.app, ["merge"])
        assert len(received_extra) == 1
        assert "rebase" in received_extra[0].lower()
        assert "UU src/foo.py" in received_extra[0]

    def test_conflict_agent_failure_exits_nonzero(self, monkeypatch, tmp_path):
        """If the coder agent fails, merge exits with its exit code."""
        self._setup(monkeypatch, tmp_path)
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)
        monkeypatch.setattr(tg, "get_messages", lambda: [])
        monkeypatch.setattr(tg, "send_message", lambda t: None)

        def fake_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 1 if cmd == ["git", "rebase", "main"] else 0
            r.stdout = ""
            return r

        monkeypatch.setattr(m.subprocess, "run", fake_run)
        monkeypatch.setattr(_git, "_conflict_status", lambda wt: "UU src/foo.py")
        monkeypatch.setattr(_ctx, "invoke_agent", lambda name, ctx, mdl, **kw: 2)
        monkeypatch.setattr(_ctx, "build_agent_context", lambda name, msgs, **kw: ("model", "ctx"))

        result = runner.invoke(m.app, ["merge"])
        assert result.exit_code == 2

    def test_rebase_still_in_progress_after_agent_exits_nonzero(self, monkeypatch, tmp_path):
        """If the agent exits 0 but rebase is still stalled, exit 1."""
        self._setup(monkeypatch, tmp_path)
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)
        monkeypatch.setattr(tg, "get_messages", lambda: [])
        monkeypatch.setattr(tg, "send_message", lambda t: None)

        def fake_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 1 if cmd == ["git", "rebase", "main"] else 0
            r.stdout = ""
            return r

        monkeypatch.setattr(m.subprocess, "run", fake_run)
        monkeypatch.setattr(_git, "_conflict_status", lambda wt: "UU src/foo.py")
        monkeypatch.setattr(_git, "_rebase_in_progress", lambda wt: True)
        monkeypatch.setattr(_ctx, "invoke_agent", lambda name, ctx, mdl, **kw: 0)
        monkeypatch.setattr(_ctx, "build_agent_context", lambda name, msgs, **kw: ("model", "ctx"))

        result = runner.invoke(m.app, ["merge"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Feature worktree lifecycle
# ---------------------------------------------------------------------------


class TestFeatureWorktree:
    def test_feature_branch_naming(self):
        assert m._feature_branch("0003-resource-type-enum.md") == "feat/0003-resource-type-enum"
        assert m._feature_branch("0001-foo.md") == "feat/0001-foo"

    def test_feature_worktree_path_is_sibling_of_dev(self):
        wt = m._feature_worktree_path("0003-resource-type-enum.md")
        assert wt.parent == m.DEV_WORKTREE.parent
        assert "feat-0003-resource-type-enum" in wt.name

    def test_active_task_name_returns_first_open(self, monkeypatch, tmp_path):
        board = tmp_path / "board.yaml"
        board.write_text("counter: 2\nopen:\n  - name: 0001-foo.md\n  - name: 0002-bar.md\n")
        monkeypatch.setattr(_cfg, "BOARD_FILE", board)
        assert m._active_task_name() == "0001-foo.md"

    def test_active_task_name_returns_none_when_empty(self, monkeypatch, tmp_path):
        board = tmp_path / "board.yaml"
        board.write_text("counter: 1\nopen: []\n")
        monkeypatch.setattr(_cfg, "BOARD_FILE", board)
        assert m._active_task_name() is None

    def test_active_task_name_returns_none_when_no_board(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_cfg, "BOARD_FILE", tmp_path / "missing.yaml")
        assert m._active_task_name() is None

    def test_ensure_feature_worktree_creates_branch_and_worktree(self, monkeypatch, tmp_path):
        runs: list[list[str]] = []

        def fake_run(cmd, cwd=None, check=False, **kw):
            runs.append(cmd)
            r = MagicMock()
            r.stdout = ""
            r.returncode = 0
            return r

        monkeypatch.setattr(m.subprocess, "run", fake_run)
        monkeypatch.setattr(_cfg, "REPO_ROOT", tmp_path)
        # Point worktree path to a non-existent directory so the worktree add is triggered.
        absent_wt = tmp_path / "feat-0001-foo"
        monkeypatch.setattr(_git, "_feature_worktree_path", lambda t: absent_wt)

        m._ensure_feature_worktree("0001-foo.md")

        cmds = [" ".join(c) for c in runs]
        assert any("branch" in c and "feat/0001-foo" in c for c in cmds), cmds
        assert any("worktree add" in c and str(absent_wt) in c for c in cmds), cmds

    def test_merge_feature_into_dev_merges_and_removes_worktree(self, monkeypatch, tmp_path):
        runs: list[list[str]] = []

        def fake_run(cmd, cwd=None, check=False, capture_output=False, text=False, **kw):
            runs.append(cmd)
            r = MagicMock()
            # Return a plausible SHA for rev-parse --short HEAD
            r.stdout = "abc1234\n" if "--short" in cmd else ""
            r.returncode = 0
            return r

        monkeypatch.setattr(m.subprocess, "run", fake_run)
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)
        monkeypatch.setattr(_cfg, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path / ".orc")

        # Create the board.yaml that _close_task_on_board expects in the dev worktree
        work_dir = tmp_path / ".orc" / "work"
        work_dir.mkdir(parents=True)
        board_yaml = work_dir / "board.yaml"
        board_yaml.write_text("counter: 1\nopen:\n  - name: 0001-foo.md\ndone: []\n")
        (work_dir / "0001-foo.md").write_text("task content")

        # Simulate worktree existing
        fake_wt = tmp_path / "colony-feat-0001-foo"
        fake_wt.mkdir()
        monkeypatch.setattr(_git, "_feature_worktree_path", lambda t: fake_wt)

        m._merge_feature_into_dev("0001-foo.md")

        cmds = [" ".join(c) for c in runs]
        assert any("merge" in c and "feat/0001-foo" in c for c in cmds), cmds
        assert any("worktree remove" in c for c in cmds), cmds
        assert any("branch" in c and "-d" in c for c in cmds), cmds
        # Board should be updated: task moved to done
        import yaml as _yaml

        updated = _yaml.safe_load(board_yaml.read_text())
        assert updated["open"] == []
        assert any(t["name"] == "0001-foo.md" for t in updated.get("done", []))

    def test_run_creates_feature_worktree_before_coder(self, monkeypatch, tmp_path):
        board = tmp_path / "board.yaml"
        board.write_text("counter: 1\nopen:\n  - name: 0001-foo.md\n")
        monkeypatch.setattr(_cfg, "BOARD_FILE", board)
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path)

        # Git-derived state: open task, no feature branch → coder
        monkeypatch.setattr(_git, "_feature_branch_exists", lambda b: False)
        monkeypatch.setattr(_git, "_feature_has_commits_ahead_of_main", lambda b: False)
        monkeypatch.setattr(_git, "_feature_merged_into_dev", lambda b: False)
        monkeypatch.setattr(_git, "_last_feature_commit_message", lambda b: None)

        monkeypatch.setattr(tg, "get_messages", lambda: [])
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])
        monkeypatch.setattr(_merge_mod, "_rebase_dev_on_main", lambda *_: None)
        monkeypatch.setattr(tg, "send_message", lambda t: None)
        monkeypatch.setattr(_ctx, "build_agent_context", lambda *a, **kw: ("model", "ctx"))
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)
        monkeypatch.setattr(inv, "spawn", lambda *a, **kw: (FakePopen(), None))
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        created: list[str] = []
        monkeypatch.setattr(
            _git, "_ensure_feature_worktree", lambda t: created.append(t) or tmp_path
        )

        runner.invoke(m.app, ["run", "--maxloops", "1"])
        assert created == ["0001-foo.md"]

    def test_run_creates_feature_worktree_before_qa(self, monkeypatch, tmp_path):
        """QA also gets a feature worktree, not the dev worktree."""
        board = tmp_path / "board.yaml"
        board.write_text("counter: 1\nopen:\n  - name: 0001-foo.md\n")
        monkeypatch.setattr(_cfg, "BOARD_FILE", board)
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path)

        # Git state: coder has commits → route to QA
        monkeypatch.setattr(_git, "_feature_branch_exists", lambda b: True)
        monkeypatch.setattr(_git, "_feature_has_commits_ahead_of_main", lambda b: True)
        monkeypatch.setattr(_git, "_feature_merged_into_dev", lambda b: False)
        monkeypatch.setattr(_git, "_last_feature_commit_message", lambda b: "feat: implement it")

        monkeypatch.setattr(tg, "get_messages", lambda: [])
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])
        monkeypatch.setattr(_merge_mod, "_rebase_dev_on_main", lambda *_: None)
        monkeypatch.setattr(tg, "send_message", lambda t: None)
        monkeypatch.setattr(_ctx, "build_agent_context", lambda *a, **kw: ("model", "ctx"))
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)
        monkeypatch.setattr(inv, "spawn", lambda *a, **kw: (FakePopen(), None))
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        created: list[str] = []
        monkeypatch.setattr(
            _git, "_ensure_feature_worktree", lambda t: created.append(t) or tmp_path
        )

        runner.invoke(m.app, ["run", "--maxloops", "1"])
        assert created == ["0001-foo.md"], "QA should get the feature worktree, not dev"

    def test_run_merges_feature_after_qa_passed_commit(self, monkeypatch, tmp_path):
        """Merge is triggered by a qa(passed): commit, not by Telegram."""
        board = tmp_path / "board.yaml"
        board.write_text("counter: 1\nopen:\n  - name: 0001-foo.md\ndone: []\n")
        monkeypatch.setattr(_cfg, "BOARD_FILE", board)
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path)

        # Git state: last commit is qa(passed) → _QA_PASSED sentinel → merge
        monkeypatch.setattr(_git, "_feature_branch_exists", lambda b: True)
        monkeypatch.setattr(_git, "_feature_has_commits_ahead_of_main", lambda b: True)
        monkeypatch.setattr(_git, "_feature_merged_into_dev", lambda b: False)
        monkeypatch.setattr(_git, "_last_feature_commit_message", lambda b: "qa(passed): all good")

        monkeypatch.setattr(tg, "get_messages", lambda: [])
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])
        monkeypatch.setattr(_merge_mod, "_rebase_dev_on_main", lambda *_: None)
        monkeypatch.setattr(tg, "send_message", lambda t: None)
        monkeypatch.setattr(_ctx, "build_agent_context", lambda *a, **kw: ("model", "ctx"))
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)
        monkeypatch.setattr(_git, "_ensure_feature_worktree", lambda t: tmp_path)
        monkeypatch.setattr(inv, "spawn", lambda *a, **kw: (FakePopen(), None))
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        merged: list[str] = []

        def fake_merge(t):
            merged.append(t)
            # Update the board so the dispatcher sees no more open tasks.
            board_data = _yaml.safe_load(board.read_text()) or {}
            board_data["open"] = [
                x
                for x in board_data.get("open", [])
                if (x["name"] if isinstance(x, dict) else x) != t
            ]
            board_data.setdefault("done", []).append({"name": t})
            board.write_text(_yaml.dump(board_data))

        monkeypatch.setattr(_git, "_merge_feature_into_dev", fake_merge)

        runner.invoke(m.app, ["run", "--maxloops", "1"])
        assert merged == ["0001-foo.md"]


# ---------------------------------------------------------------------------
# bootstrap command
# ---------------------------------------------------------------------------


class TestBootstrap:
    def test_creates_directory_structure(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(m.app, ["bootstrap"])
        assert result.exit_code == 0
        for subdir in ("roles", "squads", "vision", "work"):
            assert (tmp_path / ".orc" / subdir).is_dir()

    def test_copies_bundled_roles(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        for role in ("planner", "coder", "qa"):
            role_file = tmp_path / ".orc" / "roles" / f"{role}.md"
            assert role_file.exists(), f"Missing {role}.md"
            assert len(role_file.read_text()) > 100

    def test_copies_default_squad(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        squad_file = tmp_path / ".orc" / "squads" / "default.yaml"
        assert squad_file.exists()
        import yaml

        cfg = yaml.safe_load(squad_file.read_text())
        composition = cfg.get("composition") or cfg
        if isinstance(composition, list):
            roles = {e["role"]: e["count"] for e in composition}
            assert roles["planner"] == 1
            assert roles["coder"] == 1
        else:
            assert composition["planner"] == 1
            assert composition["coder"] == 1

    def test_creates_vision_readme(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        readme = tmp_path / ".orc" / "vision" / "README.md"
        assert readme.exists()
        assert "vision" in readme.read_text().lower()

    def test_creates_empty_board(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        board = tmp_path / ".orc" / "work" / "board.yaml"
        assert board.exists()
        import yaml

        data = yaml.safe_load(board.read_text())
        assert data["counter"] == 1
        assert data["open"] == []
        assert data["done"] == []

    def test_creates_justfile(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        justfile = tmp_path / ".orc" / "justfile"
        assert justfile.exists()
        content = justfile.read_text()
        assert "orc run" in content
        assert "orc status" in content
        assert "orc merge" in content

    def test_creates_env_example(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        env_example = tmp_path / ".env.example"
        assert env_example.exists()
        assert "COLONY_TELEGRAM_TOKEN" in env_example.read_text()

    def test_skips_existing_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        # Overwrite the justfile with sentinel content
        sentinel = "# my custom justfile"
        (tmp_path / ".orc" / "justfile").write_text(sentinel)
        runner.invoke(m.app, ["bootstrap"])
        # Should not have been overwritten
        assert (tmp_path / ".orc" / "justfile").read_text() == sentinel

    def test_force_overwrites_existing_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        (tmp_path / ".orc" / "justfile").write_text("# custom")
        runner.invoke(m.app, ["bootstrap", "--force"])
        # Should have been overwritten with generated content
        assert "orc run" in (tmp_path / ".orc" / "justfile").read_text()

    def test_custom_orc_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap", "--to", "agents"])
        assert (tmp_path / "agents" / "roles" / "planner.md").exists()
        assert (tmp_path / "agents" / "work" / "board.yaml").exists()

    def test_output_reports_created_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(m.app, ["bootstrap"])
        assert "Bootstrapped" in result.output
        assert "justfile" in result.output
        assert "Next steps" in result.output

    def test_output_reports_skipped_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        result = runner.invoke(m.app, ["bootstrap"])
        assert "Skipped" in result.output
