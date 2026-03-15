"""Tests for orc/cli/merge.py."""

import subprocess
from dataclasses import replace as _replace
from unittest.mock import MagicMock

from typer.testing import CliRunner

import orc.cli.status as _status_mod
import orc.config as _cfg
import orc.engine.context as _ctx
import orc.main as m
import orc.messaging.telegram as tg
from orc.git import Git, UntrackedMergeBlockError

runner = CliRunner()


def _fake_run_success(cmd, cwd=None, check=False, **kw):
    """Helper: returns a MagicMock with returncode=0."""
    r = MagicMock()
    r.returncode = 0
    return r


class TestMergeCommand:
    def _setup(self, monkeypatch, tmp_path, board_file, mock_validate_env, mock_git):
        """Set up test with board file and mocked config."""
        board_file("counter: 1\nopen:\n  - name: 0001-foo.md\n")
        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(
                _cfg.get(), board_file=tmp_path / ".orc" / "work" / "board.yaml", orc_dir=tmp_path
            ),
        )

    def test_clean_rebase_default_shows_manual_instructions(
        self, monkeypatch, tmp_path, board_file, mock_validate_env, mock_git
    ):
        """Default (no --auto): rebase succeeds, user is told to merge manually."""
        self._setup(monkeypatch, tmp_path, board_file, mock_validate_env, mock_git)
        monkeypatch.setattr(_status_mod, "_dev_ahead_of_main", lambda: 3)
        monkeypatch.setattr(subprocess, "run", _fake_run_success)
        completed: list[bool] = []
        monkeypatch.setattr("orc.git.Git.merge_ff_only", lambda self, b: completed.append(True))

        result = runner.invoke(m.app, ["merge"])
        assert result.exit_code == 0
        assert completed == []
        assert "--auto" in result.output

    def test_nothing_to_merge_when_dev_even_with_main(
        self, monkeypatch, tmp_path, board_file, mock_validate_env, mock_git
    ):
        """Default (no --auto): exits with 'nothing to merge' when dev has no new commits."""
        self._setup(monkeypatch, tmp_path, board_file, mock_validate_env, mock_git)
        monkeypatch.setattr(_status_mod, "_dev_ahead_of_main", lambda: 0)
        monkeypatch.setattr(subprocess, "run", _fake_run_success)

        result = runner.invoke(m.app, ["merge"])
        assert result.exit_code == 0
        assert "Nothing to merge" in result.output
        assert "--auto" not in result.output

    def test_clean_rebase_auto_completes_merge(
        self, monkeypatch, tmp_path, board_file, mock_validate_env, mock_git
    ):
        """With --auto: rebase succeeds and _complete_merge is called."""
        self._setup(monkeypatch, tmp_path, board_file, mock_validate_env, mock_git)
        monkeypatch.setattr(subprocess, "run", _fake_run_success)
        completed: list[bool] = []
        monkeypatch.setattr(
            "orc.git.Git.merge_ff_only", lambda self, b: completed.append(True) or True
        )

        result = runner.invoke(m.app, ["merge", "--auto"])
        assert result.exit_code == 0
        assert completed == [True]
        assert "merged into main" in result.output

    def test_already_up_to_date(
        self, monkeypatch, tmp_path, board_file, mock_validate_env, mock_git
    ):
        """With --auto: prints 'Already up to date.' when nothing to merge."""
        self._setup(monkeypatch, tmp_path, board_file, mock_validate_env, mock_git)
        monkeypatch.setattr(subprocess, "run", _fake_run_success)
        monkeypatch.setattr("orc.git.Git.merge_ff_only", lambda self, b: False)

        result = runner.invoke(m.app, ["merge", "--auto"])
        assert result.exit_code == 0
        assert "Already up to date" in result.output

    def test_conflict_delegates_to_coder_then_completes(
        self, monkeypatch, tmp_path, board_file, mock_validate_env, mock_git
    ):
        """On conflict the coder is invoked; with --auto the merge completes."""
        self._setup(monkeypatch, tmp_path, board_file, mock_validate_env, mock_git)
        monkeypatch.setattr(tg, "_get_messages", lambda limit=100: [])
        monkeypatch.setattr(tg, "_send_message", lambda t: None)

        call_count = 0

        def fake_run(cmd, cwd=None, check=False, **kw):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            r.returncode = 1 if cmd == ["git", "rebase", "--autostash", "main"] else 0
            r.stdout = "UU src/conflict.py\n"
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr("orc.git.Git.is_rebase_in_progress", lambda self: False)

        invocations: list[str] = []
        monkeypatch.setattr(
            _ctx, "invoke_agent", lambda name, ctx, mdl, **kw: invocations.append(name) or 0
        )
        monkeypatch.setattr(
            _ctx, "build_agent_context", lambda role, board=None, agent_id=None, **kw: "ctx"
        )
        completed: list[bool] = []
        monkeypatch.setattr(
            "orc.git.Git.merge_ff_only", lambda self, b: completed.append(True) or True
        )

        result = runner.invoke(m.app, ["merge", "--auto"])
        assert result.exit_code == 0
        assert invocations == ["coder"]
        assert completed == [True]

    def test_conflict_agent_passes_conflict_extra_context(
        self, monkeypatch, tmp_path, board_file, mock_validate_env, mock_git
    ):
        """The coder agent receives a context section describing the conflict type."""
        self._setup(monkeypatch, tmp_path, board_file, mock_validate_env, mock_git)
        monkeypatch.setattr(tg, "_get_messages", lambda limit=100: [])
        monkeypatch.setattr(tg, "_send_message", lambda t: None)

        def fake_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 1 if cmd == ["git", "rebase", "--autostash", "main"] else 0
            r.stdout = "UU src/foo.py\n" if "status" in cmd else ""
            r.args = cmd
            r.stderr = ""
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr("orc.git.Git.is_rebase_in_progress", lambda self: False)
        monkeypatch.setattr("orc.git.Git.merge_ff_only", lambda self, b: False)
        monkeypatch.setattr(
            _ctx, "build_agent_context", lambda role, board=None, agent_id=None, **kw: "ctx"
        )

        received_contexts: list[str] = []
        monkeypatch.setattr(
            _ctx,
            "invoke_agent",
            lambda name, ctx, mdl, **kw: received_contexts.append(ctx) or 0,
        )

        runner.invoke(m.app, ["merge"])
        assert len(received_contexts) == 1
        assert "rebase" in received_contexts[0].lower()

    def test_conflict_agent_failure_exits_nonzero(
        self, monkeypatch, tmp_path, board_file, mock_validate_env, mock_git
    ):
        """If the coder agent fails, merge exits with its exit code."""
        self._setup(monkeypatch, tmp_path, board_file, mock_validate_env, mock_git)
        monkeypatch.setattr(tg, "_get_messages", lambda limit=100: [])
        monkeypatch.setattr(tg, "_send_message", lambda t: None)

        def fake_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 1 if cmd == ["git", "rebase", "--autostash", "main"] else 0
            r.stdout = ""
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(_ctx, "invoke_agent", lambda name, ctx, mdl, **kw: 2)
        monkeypatch.setattr(
            _ctx, "build_agent_context", lambda role, board=None, agent_id=None, **kw: "ctx"
        )

        result = runner.invoke(m.app, ["merge"])
        assert result.exit_code == 2

    def test_rebase_still_in_progress_after_agent_exits_nonzero(
        self, monkeypatch, tmp_path, board_file, mock_validate_env, mock_git
    ):
        """If the agent exits 0 but rebase is still stalled, exit 1."""
        self._setup(monkeypatch, tmp_path, board_file, mock_validate_env, mock_git)
        monkeypatch.setattr(tg, "_get_messages", lambda limit=100: [])
        monkeypatch.setattr(tg, "_send_message", lambda t: None)

        def fake_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 1 if cmd == ["git", "rebase", "--autostash", "main"] else 0
            r.stdout = ""
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr("orc.git.Git.is_rebase_in_progress", lambda self: True)
        monkeypatch.setattr(_ctx, "invoke_agent", lambda name, ctx, mdl, **kw: 0)
        monkeypatch.setattr(
            _ctx, "build_agent_context", lambda role, board=None, agent_id=None, **kw: "ctx"
        )

        result = runner.invoke(m.app, ["merge"])
        assert result.exit_code == 1

    def test_untracked_file_conflict_exits_with_message(
        self, monkeypatch, tmp_path, board_file, mock_validate_env, mock_git
    ):
        """With --auto: if untracked files block the merge, exit 1 with a clear message."""
        self._setup(monkeypatch, tmp_path, board_file, mock_validate_env, mock_git)
        monkeypatch.setattr(subprocess, "run", _fake_run_success)

        monkeypatch.setattr(
            "orc.git.Git.merge_ff_only",
            lambda self, b: (_ for _ in ()).throw(
                UntrackedMergeBlockError([".orc/work/board.yaml"])
            ),
        )

        result = runner.invoke(m.app, ["merge", "--auto"])
        assert result.exit_code == 1
        assert "board.yaml" in result.output
        assert "untracked" in result.output.lower()

    def test_complete_merge_raises_on_untracked_files(self, monkeypatch, tmp_path):
        """_complete_merge raises UntrackedMergeBlockError when git reports untracked files."""
        from dataclasses import replace as _replace

        import pytest

        cfg = _replace(_cfg.get(), repo_root=tmp_path)
        monkeypatch.setattr(_cfg, "_config", cfg)

        stderr = (
            "error: The following untracked working tree files would be overwritten by merge:\n"
            "\t.orc/work/board.yaml\n"
            "Please move or remove them before you merge.\nAborting\n"
        )

        def fake_run(cmd, cwd=None, check=False, capture_output=False, text=False, **kw):
            r = MagicMock()
            r.returncode = 1
            r.stdout = ""
            r.stderr = stderr
            r.args = cmd
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(UntrackedMergeBlockError) as exc_info:
            Git(_cfg.get().repo_root).merge_ff_only(_cfg.get().work_dev_branch)

        assert ".orc/work/board.yaml" in exc_info.value.files

    def test_complete_merge_reraises_unknown_git_error(self, monkeypatch, tmp_path):
        """_complete_merge re-raises CalledProcessError for unexpected git failures."""
        from dataclasses import replace as _replace

        import pytest

        cfg = _replace(_cfg.get(), repo_root=tmp_path)
        monkeypatch.setattr(_cfg, "_config", cfg)

        def fake_run(cmd, cwd=None, check=False, capture_output=False, text=False, **kw):
            r = MagicMock()
            r.returncode = 1
            r.stdout = ""
            r.stderr = "fatal: some unexpected git error\n"
            r.args = cmd
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(subprocess.CalledProcessError):
            Git(_cfg.get().repo_root).merge_ff_only(_cfg.get().work_dev_branch)

    def test_manual_instructions_use_repo_root_not_dev_worktree(
        self, monkeypatch, tmp_path, board_file, mock_validate_env, mock_git
    ):
        """Without --auto: the printed git instructions use repo_root, not dev worktree."""
        self._setup(monkeypatch, tmp_path, board_file, mock_validate_env, mock_git)
        # Override mock_git's default to return a different worktree
        monkeypatch.setattr("orc.git.Git.ensure_worktree", lambda self, wt, br: None)
        monkeypatch.setattr(_status_mod, "_dev_ahead_of_main", lambda: 3)
        monkeypatch.setattr(subprocess, "run", _fake_run_success)

        result = runner.invoke(m.app, ["merge"])
        assert result.exit_code == 0
        # Must NOT suggest checking out in the dev worktree
        assert "checkout main" not in result.output
        # Must use merge --ff-only
        assert "merge --ff-only" in result.output
