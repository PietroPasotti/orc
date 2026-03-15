"""Tests for orc/git.py."""

import subprocess
from dataclasses import replace as _replace
from unittest.mock import MagicMock, patch

import pytest
import yaml

import orc.config as _cfg
import orc.git.core as _git
from orc.git.core import (
    _ensure_feature_worktree,
    _feature_branch,
    _feature_worktree_path,
    _merge_feature_into_dev,
)

# ---------------------------------------------------------------------------
# _derive_task_state (now in engine/workflow.py)
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
        board_status=None,
    ):
        monkeypatch.setattr("orc.git.core._feature_branch_exists", lambda b: branch_exists)
        monkeypatch.setattr(
            "orc.git.core._feature_has_commits_ahead_of_main", lambda b: has_commits
        )
        monkeypatch.setattr("orc.git.core._feature_merged_into_dev", lambda b: is_merged)
        self._task_data = {"name": active_task, "status": board_status} if board_status else None

    @pytest.mark.parametrize(
        "branch_exists,has_commits,is_merged,board_status,expected_agent,expected_reason_substr",
        [
            (False, False, False, None, "coder", "does not exist"),
            (True, False, False, None, "coder", "no commits"),
            (True, False, True, None, "CLOSE_BOARD_SENTINEL", "merged"),
            (True, True, False, "coding", "coder", "coding"),
            (True, True, False, None, "coder", None),  # defaults to coding
        ],
    )
    def test_derive_task_state(
        self,
        monkeypatch,
        branch_exists,
        has_commits,
        is_merged,
        board_status,
        expected_agent,
        expected_reason_substr,
    ):
        from orc.engine.dispatcher import CLOSE_BOARD
        from orc.engine.workflow import _derive_task_state

        self._patch(
            monkeypatch,
            active_task="0003-foo.md",
            branch_exists=branch_exists,
            has_commits=has_commits,
            is_merged=is_merged,
            board_status=board_status,
        )
        agent, reason = _derive_task_state("0003-foo.md", self._task_data)
        if expected_agent == "CLOSE_BOARD_SENTINEL":
            assert agent == CLOSE_BOARD
        else:
            assert agent == expected_agent
        if expected_reason_substr:
            assert expected_reason_substr in reason

    def test_reason_includes_branch_name(self, monkeypatch):
        from orc.engine.workflow import _derive_task_state

        self._patch(
            monkeypatch,
            active_task="0003-resource-type-enum.md",
            branch_exists=True,
            has_commits=True,
            board_status="coding",
        )
        task_data = {"name": "0003-resource-type-enum.md", "status": "coding"}
        _, reason = _derive_task_state("0003-resource-type-enum.md", task_data)
        assert "feat/0003-resource-type-enum" in reason


# ---------------------------------------------------------------------------
# BoardStateManager.delete_task (replaces _close_task_on_board)
# ---------------------------------------------------------------------------


class TestDeleteTask:
    def test_delete_task_removes_from_board(self, tmp_path):
        from orc.coordination.state import BoardStateManager

        orc_dir = tmp_path / ".orc"
        board_path = orc_dir / "work" / "board.yaml"
        board_path.parent.mkdir(parents=True, exist_ok=True)
        board_path.write_text("counter: 2\ntasks:\n  - name: 0002-bar.md\n  - name: 0003-foo.md\n")

        mgr = BoardStateManager(orc_dir)
        mgr.delete_task("0003-foo.md")

        board = yaml.safe_load(board_path.read_text())
        names = [t["name"] if isinstance(t, dict) else str(t) for t in board["tasks"]]
        assert "0003-foo.md" not in names
        assert "0002-bar.md" in names

    def test_delete_task_deletes_md_file(self, tmp_path):
        from orc.coordination.state import BoardStateManager

        orc_dir = tmp_path / ".orc"
        board_path = orc_dir / "work" / "board.yaml"
        board_path.parent.mkdir(parents=True, exist_ok=True)
        board_path.write_text("counter: 1\ntasks:\n  - name: 0003-foo.md\n")
        task_md = orc_dir / "work" / "0003-foo.md"
        task_md.write_text("# Task\n")

        mgr = BoardStateManager(orc_dir)
        mgr.delete_task("0003-foo.md")

        assert not task_md.exists()

    def test_delete_task_preserves_other_tasks(self, tmp_path):
        from orc.coordination.state import BoardStateManager

        orc_dir = tmp_path / ".orc"
        board_path = orc_dir / "work" / "board.yaml"
        board_path.parent.mkdir(parents=True, exist_ok=True)
        board_path.write_text("counter: 3\ntasks:\n  - name: 0003-foo.md\n  - name: 0004-bar.md\n")

        mgr = BoardStateManager(orc_dir)
        mgr.delete_task("0003-foo.md")

        board = yaml.safe_load(board_path.read_text())
        names = [t["name"] if isinstance(t, dict) else str(t) for t in board["tasks"]]
        assert "0004-bar.md" in names
        assert "0003-foo.md" not in names


# ---------------------------------------------------------------------------
# Feature worktree lifecycle
# ---------------------------------------------------------------------------


class TestFeatureWorktree:
    def test_feature_branch_naming(self):
        assert _feature_branch("0003-resource-type-enum.md") == "feat/0003-resource-type-enum"
        assert _feature_branch("0001-foo.md") == "feat/0001-foo"

    def test_feature_branch_naming_with_prefix(self, monkeypatch):
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), branch_prefix="orc"))
        assert _feature_branch("0001-foo.md") == "orc/feat/0001-foo"
        assert _feature_branch("0003-resource-type-enum.md") == "orc/feat/0003-resource-type-enum"

    def test_feature_branch_naming_empty_prefix_has_no_prefix(self, monkeypatch):
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), branch_prefix=""))
        assert _feature_branch("0001-foo.md") == "feat/0001-foo"

    def test_feature_worktree_path_under_worktree_base(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), worktree_base=tmp_path / "wt"))
        wt = _feature_worktree_path("0003-resource-type-enum.md")
        assert wt == tmp_path / "wt" / "0003-resource-type-enum"

    def test_active_task_name_returns_first_open(self, tmp_path):
        from orc.coordination.state import BoardStateManager

        orc_dir = tmp_path / ".orc"
        (orc_dir / "work").mkdir(parents=True, exist_ok=True)
        (orc_dir / "work" / "board.yaml").write_text(
            "counter: 2\ntasks:\n  - name: 0001-foo.md\n  - name: 0002-bar.md\n"
        )
        assert BoardStateManager(orc_dir).active_task_name() == "0001-foo.md"

    def test_active_task_name_returns_none_when_empty(self, tmp_path):
        from orc.coordination.state import BoardStateManager

        orc_dir = tmp_path / ".orc"
        (orc_dir / "work").mkdir(parents=True, exist_ok=True)
        (orc_dir / "work" / "board.yaml").write_text("counter: 1\ntasks: []\n")
        assert BoardStateManager(orc_dir).active_task_name() is None

    def test_ensure_feature_worktree_creates_branch_and_worktree(self, monkeypatch, tmp_path):
        runs: list[list[str]] = []

        def fake_run(cmd, cwd=None, check=False, **kw):
            runs.append(cmd)
            r = MagicMock()
            r.stdout = ""
            r.returncode = 0
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), repo_root=tmp_path))
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
        monkeypatch.setattr(
            _cfg, "_config", _replace(_cfg.get(), repo_root=tmp_path, orc_dir=tmp_path / ".orc")
        )

        fake_wt = tmp_path / "colony-feat-0001-foo"
        fake_wt.mkdir(exist_ok=True)
        monkeypatch.setattr(_git, "_feature_worktree_path", lambda t: fake_wt)

        _merge_feature_into_dev("0001-foo.md")

        cmds = [" ".join(c) for c in runs]
        assert any("merge" in c and "feat/0001-foo" in c for c in cmds), cmds
        assert any("worktree remove" in c for c in cmds), cmds
        assert any("branch" in c and "-D" in c for c in cmds), cmds
        # Board deletion is handled by the dispatcher after merge; _merge_feature_into_dev
        # is a pure git operation and does not modify board.yaml.


# ---------------------------------------------------------------------------
# git.py coverage gap tests
# ---------------------------------------------------------------------------


class TestGitCoverage:
    def test_ensure_dev_worktree_creates_if_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), dev_worktree=tmp_path / "dev-wt"))
        runs = []

        def fake_run(cmd, **kw):
            runs.append(cmd)
            r = MagicMock()
            r.returncode = 0
            return r

        with patch("orc.git.core.subprocess.run", fake_run):
            _git._ensure_dev_worktree()
        assert any("worktree" in " ".join(c) for c in runs)

    def test_rebase_in_progress_false(self, tmp_path):
        """Lines 235-244: _rebase_in_progress with real git dir returns False."""

        def fake_run(cmd, **kw):
            r = MagicMock()
            r.stdout = ".git\n"
            return r

        with patch("orc.git.core.subprocess.run", fake_run):
            result = _git._rebase_in_progress(tmp_path)
        assert result is False

    def test_complete_merge_calls_git(self, tmp_path, monkeypatch):
        """_complete_merge merges dev into main from repo_root and
        returns True when a merge occurred
        """
        monkeypatch.setattr(
            _cfg, "_config", _replace(_cfg.get(), work_dev_branch="dev", repo_root=tmp_path)
        )
        runs = []

        def fake_run(cmd, **kw):
            runs.append((cmd, kw.get("cwd")))
            r = MagicMock()
            r.returncode = 0
            r.stdout = "Updating abc..def\nFast-forward\n"
            return r

        with patch("orc.git.core.subprocess.run", fake_run):
            result = _git._complete_merge()
        cmds = [" ".join(c) for c, _ in runs]
        cwds = [cwd for _, cwd in runs]
        assert any("merge" in c for c in cmds)
        assert not any("checkout" in c for c in cmds), "should not need to checkout any branch"
        assert all(cwd == tmp_path for cwd in cwds), "merge should run from repo_root"
        assert result is True

    def test_complete_merge_returns_false_when_already_up_to_date(self, tmp_path, monkeypatch):
        """_complete_merge returns False when git reports 'Already up to date.'"""
        monkeypatch.setattr(
            _cfg, "_config", _replace(_cfg.get(), work_dev_branch="dev", repo_root=tmp_path)
        )

        def fake_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 0
            r.stdout = "Already up to date.\n"
            return r

        with patch("orc.git.core.subprocess.run", fake_run):
            result = _git._complete_merge()
        assert result is False

    def test_complete_merge_raises_on_untracked_files(self, tmp_path, monkeypatch):
        """_complete_merge raises UntrackedMergeBlockError when
        git refuses because untracked files would be overwritten."""
        monkeypatch.setattr(
            _cfg, "_config", _replace(_cfg.get(), work_dev_branch="dev", repo_root=tmp_path)
        )

        def fake_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 1
            r.stdout = ""
            r.stderr = (
                "error: The following untracked working tree files would be overwritten by merge:\n"
                "\tboard.yaml\n"
                "Please move or remove them before you merge.\n"
                "Aborting\n"
            )
            return r

        with patch("orc.git.core.subprocess.run", fake_run):
            import pytest

            with pytest.raises(_git.UntrackedMergeBlockError) as exc_info:
                _git._complete_merge()
        assert "board.yaml" in exc_info.value.files

    def test_complete_merge_raises_subprocess_error_on_other_failure(self, tmp_path, monkeypatch):
        """_complete_merge re-raises CalledProcessError for non-untracked-file failures."""
        import subprocess as _sp

        monkeypatch.setattr(
            _cfg, "_config", _replace(_cfg.get(), work_dev_branch="dev", repo_root=tmp_path)
        )

        def fake_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 128
            r.stdout = ""
            r.stderr = "fatal: not a git repository\n"
            r.check_returncode = lambda: (_ for _ in ()).throw(_sp.CalledProcessError(128, cmd))
            return r

        with patch("orc.git.core.subprocess.run", fake_run):
            import pytest

            with pytest.raises(_sp.CalledProcessError):
                _git._complete_merge()

    def test_conflict_status_returns_output(self, tmp_path):
        """Lines 256-262: _conflict_status returns git status output."""

        def fake_run(cmd, **kw):
            r = MagicMock()
            r.stdout = "UU src/conflict.py"
            return r

        with patch("orc.git.core.subprocess.run", fake_run):
            result = _git._conflict_status(tmp_path)
        assert "conflict" in result

    def test_count_features_done_counts_feat_merges(self, tmp_path, monkeypatch):
        """_count_features_done returns count of Merge feat/NNNN-* lines."""
        monkeypatch.setattr(
            _cfg, "_config", _replace(_cfg.get(), repo_root=tmp_path, work_dev_branch="dev")
        )
        log_output = (
            "abc1234 Merge feat/0001-foo into dev\n"
            "def5678 Merge feat/0002-bar into dev\n"
            "ghi9012 Merge pull request #5\n"
        )

        def fake_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 0
            r.stdout = log_output
            return r

        with patch("orc.git.core.subprocess.run", fake_run):
            result = _git._count_features_done()
        assert result == 2

    def test_count_features_done_returns_zero_on_git_error(self, tmp_path, monkeypatch):
        """_count_features_done returns 0 when git exits non-zero."""
        monkeypatch.setattr(
            _cfg, "_config", _replace(_cfg.get(), repo_root=tmp_path, work_dev_branch="dev")
        )

        def fake_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 1
            r.stdout = ""
            return r

        with patch("orc.git.core.subprocess.run", fake_run):
            result = _git._count_features_done()
        assert result == 0

    def test_merge_feature_updates_board(self, tmp_path, monkeypatch):
        """_merge_feature_into_dev is a pure git operation; board deletion is done by dispatcher."""
        import orc.config as _cfg
        import orc.git.core as _git

        orc_dir = tmp_path / ".orc"
        work_dir = orc_dir / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        board_yaml = work_dir / "board.yaml"
        board_yaml.write_text("counter: 1\ntasks:\n  - name: 0001-task.md\n")

        feat_wt = tmp_path / "feat"
        feat_wt.mkdir(exist_ok=True)
        dev_wt = tmp_path / "dev"
        dev_wt.mkdir(exist_ok=True)

        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(_cfg.get(), orc_dir=orc_dir, repo_root=tmp_path, work_dev_branch="dev"),
        )
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: dev_wt)
        monkeypatch.setattr(_git, "_feature_worktree_path", lambda t: feat_wt)

        runs = []

        def fake_run(cmd, **kw):
            runs.append(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stdout = "abc1234\n"
            return r

        with patch("orc.git.core.subprocess.run", fake_run):
            _git._merge_feature_into_dev("0001-task.md")

        # Git operations: merge, worktree remove, branch delete
        cmds_str = [" ".join(c) for c in runs]
        assert any("merge" in c for c in cmds_str), cmds_str
        assert any("worktree remove" in c or "branch" in c for c in cmds_str), cmds_str

        # Board is NOT modified by _merge_feature_into_dev (dispatcher calls board.delete_task).
        board = yaml.safe_load(board_yaml.read_text())
        assert "0001-task.md" in [
            t.get("name") for t in board.get("tasks", []) if isinstance(t, dict)
        ]

        # No board-commit: git commands are merge/worktree/branch only
        assert not any("commit" in c and ".orc" in c for c in cmds_str), (
            "board must not be committed to git: " + str(cmds_str)
        )

    def test_merge_feature_raises_merge_conflict_error_on_conflict(self, tmp_path, monkeypatch):
        """When git merge fails, MergeConflictError is raised (no --abort) so coder can resolve."""
        import orc.config as _cfg
        import orc.git.core as _git

        dev_wt = tmp_path / "dev"
        dev_wt.mkdir(exist_ok=True)
        feat_wt = tmp_path / "feat"
        feat_wt.mkdir(exist_ok=True)

        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(
                _cfg.get(), orc_dir=dev_wt / ".orc", repo_root=tmp_path, work_dev_branch="dev"
            ),
        )
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

        with patch("orc.git.core.subprocess.run", fake_run):
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
        import orc.git.core as _git

        dev_wt = tmp_path / "dev"
        dev_wt.mkdir(exist_ok=True)
        (dev_wt / ".orc").mkdir(exist_ok=True)  # needed for changelog write
        feat_wt = tmp_path / "feat"
        feat_wt.mkdir(exist_ok=True)

        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(
                _cfg.get(), orc_dir=dev_wt / ".orc", repo_root=tmp_path, work_dev_branch="dev"
            ),
        )
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

        with patch("orc.git.core.subprocess.run", fake_run):
            _git._merge_feature_into_dev("0001-task.md")

        cmds_str = [" ".join(c) for c in runs]
        assert any("reset" in c and "--hard" in c and "HEAD" in c for c in cmds_str), (
            "expected git reset --hard HEAD when dev worktree is dirty"
        )

    def test_merge_feature_writes_changelog(self, tmp_path, monkeypatch):
        """_merge_feature_into_dev appends an entry to orc-CHANGELOG.md after a successful merge."""
        import orc.config as _cfg
        import orc.git.core as _git

        orc_dir = tmp_path / ".orc"
        work_dir = orc_dir / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        board_yaml = work_dir / "board.yaml"
        board_yaml.write_text("counter: 1\ntasks:\n  - name: 0001-task.md\n")

        feat_wt = tmp_path / "feat"
        feat_wt.mkdir(exist_ok=True)
        dev_wt = tmp_path / "dev"
        dev_wt.mkdir(exist_ok=True)

        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(_cfg.get(), orc_dir=orc_dir, repo_root=tmp_path, work_dev_branch="dev"),
        )
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: dev_wt)
        monkeypatch.setattr(_git, "_feature_worktree_path", lambda t: feat_wt)

        def fake_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 0
            r.stdout = "abc1234\n"
            return r

        with patch("orc.git.core.subprocess.run", fake_run):
            _git._merge_feature_into_dev("0001-task.md")

        changelog = orc_dir / "orc-CHANGELOG.md"
        assert changelog.exists(), "orc-CHANGELOG.md should be created after merge"
        text = changelog.read_text()
        assert "0001-task" in text
        assert "feat/0001-task" in text
        assert "abc1234" in text

    def test_merge_feature_appends_to_existing_changelog(self, tmp_path, monkeypatch):
        """_merge_feature_into_dev appends without overwriting an existing changelog."""
        import orc.config as _cfg
        import orc.git.core as _git

        orc_dir = tmp_path / ".orc"
        work_dir = orc_dir / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        board_yaml = work_dir / "board.yaml"
        board_yaml.write_text("counter: 1\ntasks:\n  - name: 0002-bar.md\n")
        (orc_dir / "orc-CHANGELOG.md").write_text("# Changelog\n\n## prior entry\n")

        feat_wt = tmp_path / "feat"
        feat_wt.mkdir(exist_ok=True)
        dev_wt = tmp_path / "dev"
        dev_wt.mkdir(exist_ok=True)

        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(_cfg.get(), orc_dir=orc_dir, repo_root=tmp_path, work_dev_branch="dev"),
        )
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: dev_wt)
        monkeypatch.setattr(_git, "_feature_worktree_path", lambda t: feat_wt)

        def fake_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 0
            r.stdout = "def5678\n"
            return r

        with patch("orc.git.core.subprocess.run", fake_run):
            _git._merge_feature_into_dev("0002-bar.md")

        text = (orc_dir / "orc-CHANGELOG.md").read_text()
        assert "prior entry" in text, "existing content should be preserved"
        assert "0002-bar" in text
        assert "def5678" in text

    def test_is_worktree_dirty_true(self, tmp_path):
        def fake_run(cmd, **kw):
            r = MagicMock()
            r.stdout = " M src/foo.py\n"
            return r

        with patch("orc.git.core.subprocess.run", fake_run):
            assert _git._is_worktree_dirty(tmp_path) is True

    def test_is_worktree_dirty_false(self, tmp_path):
        def fake_run(cmd, **kw):
            r = MagicMock()
            r.stdout = ""
            return r

        with patch("orc.git.core.subprocess.run", fake_run):
            assert _git._is_worktree_dirty(tmp_path) is False

    def test_merge_in_progress_true(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir(exist_ok=True)
        (git_dir / "MERGE_HEAD").write_text("abc1234\n")

        def fake_run(cmd, **kw):
            r = MagicMock()
            r.stdout = ".git\n"
            return r

        with patch("orc.git.core.subprocess.run", fake_run):
            assert _git._merge_in_progress(tmp_path) is True

    def test_merge_in_progress_false(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir(exist_ok=True)

        def fake_run(cmd, **kw):
            r = MagicMock()
            r.stdout = ".git\n"
            return r

        with patch("orc.git.core.subprocess.run", fake_run):
            assert _git._merge_in_progress(tmp_path) is False

    def test_feature_merged_into_dev_returns_true(self, monkeypatch):
        with patch("orc.git.core.subprocess.run", return_value=MagicMock(returncode=0)):
            assert _git._feature_merged_into_dev("feat/0001-foo") is True

    def test_feature_merged_into_dev_returns_false(self, monkeypatch):
        with patch("orc.git.core.subprocess.run", return_value=MagicMock(returncode=1)):
            assert _git._feature_merged_into_dev("feat/0001-foo") is False


# ---------------------------------------------------------------------------
# _derive_task_state — board status routing
# ---------------------------------------------------------------------------


class TestDeriveTaskStateBoardStatus:
    """Tests for board-status-based routing in _derive_task_state (engine/workflow.py)."""

    def _patch(self, monkeypatch, *, board_status):
        monkeypatch.setattr("orc.git.core._feature_branch_exists", lambda b: True)
        monkeypatch.setattr("orc.git.core._feature_has_commits_ahead_of_main", lambda b: True)
        monkeypatch.setattr("orc.git.core._feature_merged_into_dev", lambda b: False)

    def test_review_status_routes_to_qa(self, monkeypatch):
        from orc.engine.workflow import _derive_task_state

        self._patch(monkeypatch, board_status="in-review")
        task_data = {"name": "0002-foo.md", "status": "in-review"}
        agent, reason = _derive_task_state("0002-foo.md", task_data)
        assert agent == "qa"
        assert "awaiting QA" in reason

    def test_approved_status_routes_to_qa_passed(self, monkeypatch):
        from orc.engine.dispatcher import QA_PASSED
        from orc.engine.workflow import _derive_task_state

        self._patch(monkeypatch, board_status="done")
        task_data = {"name": "0002-foo.md", "status": "done"}
        agent, reason = _derive_task_state("0002-foo.md", task_data)
        assert agent == QA_PASSED
        assert "ready to merge" in reason

    def test_rejected_status_routes_to_coder(self, monkeypatch):
        from orc.engine.workflow import _derive_task_state

        self._patch(monkeypatch, board_status="in-progress")
        task_data = {"name": "0002-foo.md", "status": "in-progress"}
        agent, reason = _derive_task_state("0002-foo.md", task_data)
        assert agent == "coder"

    def test_coding_status_routes_to_coder(self, monkeypatch):
        from orc.engine.workflow import _derive_task_state

        self._patch(monkeypatch, board_status="in-progress")
        task_data = {"name": "0002-foo.md", "status": "in-progress"}
        agent, _ = _derive_task_state("0002-foo.md", task_data)
        assert agent == "coder"


class TestRebaseOnMain:
    """Tests for _rebase_on_main (extracted from merge.py)."""

    def test_rebase_on_main_success(self, monkeypatch):
        """Returns (True, '') when git rebase exits 0."""
        with patch("orc.git.core.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            ok, conflict = _git._rebase_on_main(MagicMock())
        assert ok is True
        assert conflict == ""

    def test_rebase_on_main_conflict(self, monkeypatch, tmp_path):
        """Returns (False, conflict_status) when git rebase exits non-zero."""
        call_count = 0

        def fake_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            # First call: the rebase (fails); second call: git status --short
            r.returncode = 1 if call_count == 1 else 0
            r.stdout = "" if call_count == 1 else "UU src/conflict.py"
            return r

        with patch("orc.git.core.subprocess.run", side_effect=fake_run):
            ok, conflict = _git._rebase_on_main(tmp_path)
        assert ok is False
        assert "UU" in conflict


class TestCountFeaturesDone:
    """Tests for _count_features_done."""

    def test_count_features_done_empty(self, monkeypatch):
        """Returns 0 when git log returns no output."""
        with patch("orc.git.core.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            assert _git._count_features_done() == 0

    def test_count_features_done_three(self, monkeypatch):
        """Returns 3 when git log returns 3 merge commits."""
        log_output = (
            "abc1234 Merge feat/0001-foo into dev\n"
            "def5678 Merge feat/0002-bar into dev\n"
            "ghi9012 Merge feat/0003-baz into dev\n"
        )
        with patch("orc.git.core.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=log_output)
            assert _git._count_features_done() == 3

    def test_count_features_done_git_error(self, monkeypatch):
        """Returns 0 when git returns non-zero exit code."""
        with patch("orc.git.core.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="")
            assert _git._count_features_done() == 0


class TestFeaturesInDevNotMain:
    """Tests for _features_in_dev_not_main."""

    def test_returns_empty_when_no_merges(self):
        """Returns [] when git log produces no output."""
        with patch("orc.git.core.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            assert _git._features_in_dev_not_main() == []

    def test_returns_empty_on_git_error(self):
        """Returns [] when git exits non-zero."""
        with patch("orc.git.core.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="")
            assert _git._features_in_dev_not_main() == []

    def test_extracts_branch_names_no_prefix(self, monkeypatch):
        """Extracts branch names from merge commit subjects (no branch prefix)."""
        log_output = (
            "abc1234 Merge feat/0001-foo into dev\n"
            "def5678 Merge feat/0002-bar into dev\n"
            "ghi9012 Merge feat/0003-baz into dev\n"
        )
        with patch("orc.git.core.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=log_output)
            result = _git._features_in_dev_not_main()
        assert result == ["feat/0001-foo", "feat/0002-bar", "feat/0003-baz"]

    def test_extracts_branch_names_with_prefix(self, monkeypatch, tmp_path):
        """Extracts branch names when orc-branch-prefix is set."""
        from dataclasses import replace as _replace

        import orc.config as _cfg

        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(_cfg.get(), branch_prefix="orc"),
        )
        log_output = (
            "abc1234 Merge orc/feat/0001-foo into dev\ndef5678 Merge orc/feat/0002-bar into dev\n"
        )
        with patch("orc.git.core.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=log_output)
            result = _git._features_in_dev_not_main()
        assert result == ["orc/feat/0001-foo", "orc/feat/0002-bar"]

    def test_ignores_non_feat_merge_commits(self):
        """Skips merge commits that don't match the feat/* pattern."""
        log_output = (
            "abc1234 Merge feat/0001-foo into dev\n"
            "xyz9999 Merge refactor/phase2-dedup: Phase 2\n"
            "def5678 chore: merge dev into main\n"
        )
        with patch("orc.git.core.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=log_output)
            result = _git._features_in_dev_not_main()
        assert result == ["feat/0001-foo"]

    def test_count_features_done_delegates(self):
        """_count_features_done returns len(_features_in_dev_not_main())."""
        log_output = "abc1234 Merge feat/0001-foo into dev\ndef5678 Merge feat/0002-bar into dev\n"
        with patch("orc.git.core.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=log_output)
            assert _git._count_features_done() == 2
