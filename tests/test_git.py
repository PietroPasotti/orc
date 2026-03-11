"""Tests for orc/git.py."""

import subprocess
from unittest.mock import MagicMock, patch

import yaml

import orc.config as _cfg
import orc.git as _git
from orc.board import _active_task_name
from orc.config import DEV_WORKTREE
from orc.git import (
    _close_task_on_board,
    _derive_state_from_git,
    _ensure_feature_worktree,
    _feature_branch,
    _feature_worktree_path,
    _merge_feature_into_dev,
)

# ---------------------------------------------------------------------------
# _derive_state_from_git
# ---------------------------------------------------------------------------


class TestDeriveStateFromGit:
    def _patch(
        self,
        monkeypatch,
        *,
        active_task,
        branch_exists,
        has_commits,
        is_merged=False,
        last_commit_msg=None,
    ):
        monkeypatch.setattr("orc.board._active_task_name", lambda: active_task)
        monkeypatch.setattr("orc.git._feature_branch_exists", lambda b: branch_exists)
        monkeypatch.setattr("orc.git._feature_has_commits_ahead_of_main", lambda b: has_commits)
        monkeypatch.setattr("orc.git._feature_merged_into_dev", lambda b: is_merged)
        monkeypatch.setattr("orc.git._last_feature_commit_message", lambda b: last_commit_msg)

    def test_no_open_tasks_returns_planner(self, monkeypatch):
        self._patch(monkeypatch, active_task=None, branch_exists=False, has_commits=False)
        agent, reason = _derive_state_from_git()
        assert agent == "planner"
        assert "no open tasks" in reason

    def test_no_feature_branch_returns_coder(self, monkeypatch):
        self._patch(
            monkeypatch,
            active_task="0003-foo.md",
            branch_exists=False,
            has_commits=False,
        )
        agent, reason = _derive_state_from_git()
        assert agent == "coder"
        assert "does not exist" in reason

    def test_no_branch_but_merged_returns_close_board(self, monkeypatch):
        """Crash recovery: branch merged but board not yet updated."""
        self._patch(
            monkeypatch,
            active_task="0003-foo.md",
            branch_exists=False,
            has_commits=False,
            is_merged=True,
        )
        agent, reason = _derive_state_from_git()
        from orc.dispatcher import CLOSE_BOARD

        assert agent == CLOSE_BOARD
        assert "merged" in reason

    def test_feature_branch_exists_no_commits_returns_coder(self, monkeypatch):
        self._patch(
            monkeypatch,
            active_task="0003-foo.md",
            branch_exists=True,
            has_commits=False,
        )
        agent, reason = _derive_state_from_git()
        assert agent == "coder"
        assert "no commits" in reason

    def test_coder_commits_returns_qa(self, monkeypatch):
        """Coder-authored commit → route to QA."""
        self._patch(
            monkeypatch,
            active_task="0003-foo.md",
            branch_exists=True,
            has_commits=True,
            last_commit_msg="feat: implement ResourceType enum",
        )
        agent, reason = _derive_state_from_git()
        assert agent == "qa"
        assert "awaiting review" in reason

    def test_qa_passed_commit_returns_qa_passed_sentinel(self, monkeypatch):
        """qa(passed): commit → _QA_PASSED sentinel."""
        self._patch(
            monkeypatch,
            active_task="0003-foo.md",
            branch_exists=True,
            has_commits=True,
            last_commit_msg="qa(passed): no issues found",
        )
        from orc.dispatcher import QA_PASSED

        agent, reason = _derive_state_from_git()
        assert agent == QA_PASSED
        assert "ready to merge" in reason

    def test_qa_failed_commit_returns_coder(self, monkeypatch):
        """qa(failed): commit → route back to coder."""
        self._patch(
            monkeypatch,
            active_task="0003-foo.md",
            branch_exists=True,
            has_commits=True,
            last_commit_msg="qa(failed): missing endpoint",
        )
        agent, reason = _derive_state_from_git()
        assert agent == "coder"
        assert "issues" in reason

    def test_qa_blocked_commit_returns_coder(self, monkeypatch):
        """Any qa( prefix other than passed → route to coder."""
        self._patch(
            monkeypatch,
            active_task="0003-foo.md",
            branch_exists=True,
            has_commits=True,
            last_commit_msg="qa(blocked): cannot review without spec",
        )
        agent, reason = _derive_state_from_git()
        assert agent == "coder"

    def test_no_last_commit_message_returns_qa(self, monkeypatch):
        """No commit message (e.g. git error) → treat as coder commits, route to QA."""
        self._patch(
            monkeypatch,
            active_task="0003-foo.md",
            branch_exists=True,
            has_commits=True,
            last_commit_msg=None,
        )
        agent, _ = _derive_state_from_git()
        assert agent == "qa"

    def test_reason_includes_branch_name(self, monkeypatch):
        self._patch(
            monkeypatch,
            active_task="0003-resource-type-enum.md",
            branch_exists=True,
            has_commits=True,
            last_commit_msg="feat: add enum",
        )
        _, reason = _derive_state_from_git()
        assert "feat/0003-resource-type-enum" in reason


# ---------------------------------------------------------------------------
# Board reconciliation (_close_task_on_board)
# ---------------------------------------------------------------------------


class TestBoardReconciliation:
    def test_close_task_moves_to_done(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path / ".orc")
        monkeypatch.setattr(_cfg, "REPO_ROOT", tmp_path)

        board_path = tmp_path / ".orc" / "work" / "board.yaml"
        board_path.parent.mkdir(parents=True)
        existing_done = (
            "done:\n  - name: 0002-bar.md\n    commit-tag: abc\n"
            "    timestamp: 2026-01-01T00:00:00Z\n"
        )
        board_path.write_text("counter: 2\nopen:\n  - name: 0003-foo.md\n" + existing_done)

        _close_task_on_board("0003-foo.md", tmp_path, commit_tag="deadbeef")

        board = yaml.safe_load(board_path.read_text())
        names_open = [t["name"] if isinstance(t, dict) else str(t) for t in board["open"]]
        names_done = [t["name"] if isinstance(t, dict) else str(t) for t in board["done"]]
        assert "0003-foo.md" not in names_open
        assert "0003-foo.md" in names_done
        done_entry = next(t for t in board["done"] if t.get("name") == "0003-foo.md")
        assert done_entry["commit-tag"] == "deadbeef"

    def test_close_task_deletes_md_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path / ".orc")
        monkeypatch.setattr(_cfg, "REPO_ROOT", tmp_path)

        board_path = tmp_path / ".orc" / "work" / "board.yaml"
        board_path.parent.mkdir(parents=True)
        board_path.write_text("counter: 1\nopen:\n  - name: 0003-foo.md\ndone: []\n")
        task_md = tmp_path / ".orc" / "work" / "0003-foo.md"
        task_md.write_text("# Task\n")

        _close_task_on_board("0003-foo.md", tmp_path, commit_tag="abc123")

        assert not task_md.exists()

    def test_close_task_missing_board_does_not_raise(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path / ".orc")
        monkeypatch.setattr(_cfg, "REPO_ROOT", tmp_path)

        _close_task_on_board("0003-foo.md", tmp_path, commit_tag="abc")

    def test_close_task_other_tasks_preserved(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path / ".orc")
        monkeypatch.setattr(_cfg, "REPO_ROOT", tmp_path)

        board_path = tmp_path / ".orc" / "work" / "board.yaml"
        board_path.parent.mkdir(parents=True)
        board_path.write_text(
            "counter: 3\nopen:\n  - name: 0003-foo.md\n  - name: 0004-bar.md\ndone: []\n"
        )

        _close_task_on_board("0003-foo.md", tmp_path, commit_tag="abc")

        board = yaml.safe_load(board_path.read_text())
        names_open = [t["name"] if isinstance(t, dict) else str(t) for t in board["open"]]
        assert "0004-bar.md" in names_open
        assert "0003-foo.md" not in names_open


# ---------------------------------------------------------------------------
# Feature worktree lifecycle
# ---------------------------------------------------------------------------


class TestFeatureWorktree:
    def test_feature_branch_naming(self):
        assert _feature_branch("0003-resource-type-enum.md") == "feat/0003-resource-type-enum"
        assert _feature_branch("0001-foo.md") == "feat/0001-foo"

    def test_feature_branch_naming_with_prefix(self, monkeypatch):
        monkeypatch.setattr(_cfg, "BRANCH_PREFIX", "orc")
        assert _feature_branch("0001-foo.md") == "orc/feat/0001-foo"
        assert _feature_branch("0003-resource-type-enum.md") == "orc/feat/0003-resource-type-enum"

    def test_feature_branch_naming_empty_prefix_has_no_prefix(self, monkeypatch):
        monkeypatch.setattr(_cfg, "BRANCH_PREFIX", "")
        assert _feature_branch("0001-foo.md") == "feat/0001-foo"

    def test_feature_worktree_path_is_sibling_of_dev(self):
        wt = _feature_worktree_path("0003-resource-type-enum.md")
        assert wt.parent == DEV_WORKTREE.parent
        assert "feat-0003-resource-type-enum" in wt.name

    def test_active_task_name_returns_first_open(self, monkeypatch, tmp_path):
        board = tmp_path / "board.yaml"
        board.write_text("counter: 2\nopen:\n  - name: 0001-foo.md\n  - name: 0002-bar.md\n")
        monkeypatch.setattr(_cfg, "BOARD_FILE", board)
        assert _active_task_name() == "0001-foo.md"

    def test_active_task_name_returns_none_when_empty(self, monkeypatch, tmp_path):
        board = tmp_path / "board.yaml"
        board.write_text("counter: 1\nopen: []\n")
        monkeypatch.setattr(_cfg, "BOARD_FILE", board)
        assert _active_task_name() is None

    def test_active_task_name_returns_none_when_no_board(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_cfg, "BOARD_FILE", tmp_path / "missing.yaml")
        assert _active_task_name() is None

    def test_ensure_feature_worktree_creates_branch_and_worktree(self, monkeypatch, tmp_path):
        runs: list[list[str]] = []

        def fake_run(cmd, cwd=None, check=False, **kw):
            runs.append(cmd)
            r = MagicMock()
            r.stdout = ""
            r.returncode = 0
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(_cfg, "REPO_ROOT", tmp_path)
        absent_wt = tmp_path / "feat-0001-foo"
        monkeypatch.setattr(_git, "_feature_worktree_path", lambda t: absent_wt)

        _ensure_feature_worktree("0001-foo.md")

        cmds = [" ".join(c) for c in runs]
        assert any("branch" in c and "feat/0001-foo" in c for c in cmds), cmds
        assert any("worktree add" in c and str(absent_wt) in c for c in cmds), cmds

    def test_merge_feature_into_dev_merges_and_removes_worktree(self, monkeypatch, tmp_path):
        runs: list[list[str]] = []

        def fake_run(cmd, cwd=None, check=False, capture_output=False, text=False, **kw):
            runs.append(cmd)
            r = MagicMock()
            r.stdout = "abc1234\n" if "--short" in cmd else ""
            r.returncode = 0
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)
        monkeypatch.setattr(_cfg, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path / ".orc")

        work_dir = tmp_path / ".orc" / "work"
        work_dir.mkdir(parents=True)
        board_yaml = work_dir / "board.yaml"
        board_yaml.write_text("counter: 1\nopen:\n  - name: 0001-foo.md\ndone: []\n")
        (work_dir / "0001-foo.md").write_text("task content")

        fake_wt = tmp_path / "colony-feat-0001-foo"
        fake_wt.mkdir()
        monkeypatch.setattr(_git, "_feature_worktree_path", lambda t: fake_wt)

        _merge_feature_into_dev("0001-foo.md")

        cmds = [" ".join(c) for c in runs]
        assert any("merge" in c and "feat/0001-foo" in c for c in cmds), cmds
        assert any("worktree remove" in c for c in cmds), cmds
        assert any("branch" in c and "-d" in c for c in cmds), cmds
        updated = yaml.safe_load(board_yaml.read_text())
        assert updated["open"] == []
        assert any(t["name"] == "0001-foo.md" for t in updated.get("done", []))


# ---------------------------------------------------------------------------
# git.py coverage gap tests
# ---------------------------------------------------------------------------


class TestGitCoverage:
    def test_ensure_dev_worktree_creates_if_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_cfg, "DEV_WORKTREE", tmp_path / "dev-wt")
        runs = []

        def fake_run(cmd, **kw):
            runs.append(cmd)
            r = MagicMock()
            r.returncode = 0
            return r

        with patch("orc.git.subprocess.run", fake_run):
            _git._ensure_dev_worktree()
        assert any("worktree" in " ".join(c) for c in runs)

    def test_close_task_on_board_missing_board(self, tmp_path, monkeypatch):
        """Lines 142-148: board.yaml not found in dev worktree → warns and returns."""
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path / "orc")
        monkeypatch.setattr(_cfg, "REPO_ROOT", tmp_path)
        dev_wt = tmp_path / "dev"
        dev_wt.mkdir()
        _git._close_task_on_board("0001-foo.md", dev_wt)  # no board → no crash

    def test_rebase_in_progress_false(self, tmp_path):
        """Lines 235-244: _rebase_in_progress with real git dir returns False."""

        def fake_run(cmd, **kw):
            r = MagicMock()
            r.stdout = ".git\n"
            return r

        with patch("orc.git.subprocess.run", fake_run):
            result = _git._rebase_in_progress(tmp_path)
        assert result is False

    def test_complete_merge_calls_git(self, tmp_path, monkeypatch):
        """Lines 249-251: _complete_merge runs checkout and merge."""
        monkeypatch.setattr(_cfg, "WORK_DEV_BRANCH", "dev")
        runs = []

        def fake_run(cmd, **kw):
            runs.append(cmd)
            r = MagicMock()
            r.returncode = 0
            return r

        with patch("orc.git.subprocess.run", fake_run):
            _git._complete_merge(tmp_path)
        cmds = [" ".join(c) for c in runs]
        assert any("checkout" in c and "main" in c for c in cmds)
        assert any("merge" in c for c in cmds)

    def test_conflict_status_returns_output(self, tmp_path):
        """Lines 256-262: _conflict_status returns git status output."""

        def fake_run(cmd, **kw):
            r = MagicMock()
            r.stdout = "UU src/conflict.py"
            return r

        with patch("orc.git.subprocess.run", fake_run):
            result = _git._conflict_status(tmp_path)
        assert "conflict" in result

    def test_close_task_agents_dir_not_relative_to_repo_root(self, tmp_path, monkeypatch):
        """Lines 86-87: AGENTS_DIR outside REPO_ROOT uses basename fallback."""
        import orc.config as _cfg
        import orc.git as _git

        agents_dir = tmp_path / "other" / "orc"
        agents_dir.mkdir(parents=True)
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        monkeypatch.setattr(_cfg, "AGENTS_DIR", agents_dir)
        monkeypatch.setattr(_cfg, "REPO_ROOT", repo_root)
        dev_wt = tmp_path / "dev"
        dev_wt.mkdir()
        # board.yaml not in dev_wt → function returns early (warning path)
        _git._close_task_on_board("0001-task.md", dev_wt)

    def test_merge_feature_commits_board_when_board_exists(self, tmp_path, monkeypatch):
        """Lines 142-148: _merge_feature_into_dev commits board when board.yaml present."""
        import orc.config as _cfg
        import orc.git as _git

        # Set up minimal git-shaped directory structure
        dev_wt = tmp_path / "dev"
        (dev_wt / "orc" / "work").mkdir(parents=True)
        (dev_wt / "orc" / "work" / "board.yaml").write_text("open: []\ndone: []\n")
        feat_wt = tmp_path / "feat"
        feat_wt.mkdir()

        monkeypatch.setattr(_cfg, "AGENTS_DIR", dev_wt / "orc")
        monkeypatch.setattr(_cfg, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(_cfg, "WORK_DEV_BRANCH", "dev")
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: dev_wt)
        monkeypatch.setattr(_git, "_feature_worktree_path", lambda t: feat_wt)

        from unittest.mock import MagicMock

        runs = []

        def fake_run(cmd, **kw):
            runs.append(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stdout = "abc1234\n"
            return r

        with patch("orc.git.subprocess.run", fake_run):
            _git._merge_feature_into_dev("0001-task.md")

        cmds_str = [" ".join(c) for c in runs]
        assert any("commit" in c for c in cmds_str)

    def test_merge_feature_raises_merge_conflict_error_on_conflict(self, tmp_path, monkeypatch):
        """When git merge fails, MergeConflictError is raised (no --abort) so coder can resolve."""
        import orc.config as _cfg
        import orc.git as _git

        dev_wt = tmp_path / "dev"
        dev_wt.mkdir()
        feat_wt = tmp_path / "feat"
        feat_wt.mkdir()

        monkeypatch.setattr(_cfg, "AGENTS_DIR", dev_wt / "orc")
        monkeypatch.setattr(_cfg, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(_cfg, "WORK_DEV_BRANCH", "dev")
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: dev_wt)
        monkeypatch.setattr(_git, "_feature_worktree_path", lambda t: feat_wt)

        runs = []

        def fake_run(cmd, **kw):
            runs.append(cmd)
            r = MagicMock()
            r.args = cmd
            r.returncode = 1 if "--no-ff" in cmd else 0
            r.stdout = "UU src/conflict.py\n"
            return r

        import pytest

        with patch("orc.git.subprocess.run", fake_run):
            with pytest.raises(_git.MergeConflictError) as exc_info:
                _git._merge_feature_into_dev("0001-task.md")

        assert exc_info.value.branch == "feat/0001-task"
        assert exc_info.value.worktree == dev_wt
        cmds_str = [" ".join(c) for c in runs]
        assert not any("--abort" in c for c in cmds_str), (
            "git merge --abort must NOT be called; leave merge in progress for coder"
        )

    def test_merge_feature_resets_dirty_dev_before_merge(self, tmp_path, monkeypatch):
        """When dev worktree is dirty, git reset --hard HEAD is called before the merge."""
        import orc.config as _cfg
        import orc.git as _git

        dev_wt = tmp_path / "dev"
        dev_wt.mkdir()
        feat_wt = tmp_path / "feat"
        feat_wt.mkdir()

        monkeypatch.setattr(_cfg, "AGENTS_DIR", dev_wt / "orc")
        monkeypatch.setattr(_cfg, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(_cfg, "WORK_DEV_BRANCH", "dev")
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: dev_wt)
        monkeypatch.setattr(_git, "_feature_worktree_path", lambda t: feat_wt)
        monkeypatch.setattr(_git, "_is_worktree_dirty", lambda p: True)

        runs = []

        def fake_run(cmd, **kw):
            runs.append(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stdout = "abc1234\n" if "--short" in cmd else ""
            return r

        with patch("orc.git.subprocess.run", fake_run):
            _git._merge_feature_into_dev("0001-task.md")

        cmds_str = [" ".join(c) for c in runs]
        assert any("reset" in c and "--hard" in c and "HEAD" in c for c in cmds_str), (
            "expected git reset --hard HEAD when dev worktree is dirty"
        )

    def test_is_worktree_dirty_true(self, tmp_path):
        def fake_run(cmd, **kw):
            r = MagicMock()
            r.stdout = " M src/foo.py\n"
            return r

        with patch("orc.git.subprocess.run", fake_run):
            assert _git._is_worktree_dirty(tmp_path) is True

    def test_is_worktree_dirty_false(self, tmp_path):
        def fake_run(cmd, **kw):
            r = MagicMock()
            r.stdout = ""
            return r

        with patch("orc.git.subprocess.run", fake_run):
            assert _git._is_worktree_dirty(tmp_path) is False

    def test_merge_in_progress_true(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("abc1234\n")

        def fake_run(cmd, **kw):
            r = MagicMock()
            r.stdout = ".git\n"
            return r

        with patch("orc.git.subprocess.run", fake_run):
            assert _git._merge_in_progress(tmp_path) is True

    def test_merge_in_progress_false(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        def fake_run(cmd, **kw):
            r = MagicMock()
            r.stdout = ".git\n"
            return r

        with patch("orc.git.subprocess.run", fake_run):
            assert _git._merge_in_progress(tmp_path) is False
