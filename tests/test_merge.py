"""Tests for orc/cli/merge.py."""

from unittest.mock import MagicMock

from typer.testing import CliRunner

import orc.config as _cfg
import orc.context as _ctx
import orc.git as _git
import orc.main as m
import orc.telegram as tg

runner = CliRunner()


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

    def test_rebase_unstaged_changes_exits_one(self, monkeypatch, tmp_path):
        """Lines 32-38: rebase fails with 'unstaged changes' → exit 1."""
        self._setup(monkeypatch, tmp_path)
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)

        def fake_run(cmd, **kw):
            r = MagicMock()
            if cmd == ["git", "rebase", "main"]:
                r.returncode = 1
                r.stderr = "error: Cannot rebase: You have unstaged changes."
                r.stdout = ""
            else:
                r.returncode = 0
                r.stdout = ""
                r.stderr = ""
            return r

        monkeypatch.setattr(m.subprocess, "run", fake_run)
        monkeypatch.setattr(_git, "_conflict_status", lambda wt: "M src/foo.py")

        result = runner.invoke(m.app, ["merge"])
        assert result.exit_code == 1
        assert "unstaged changes" in result.output
