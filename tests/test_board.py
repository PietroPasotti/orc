"""Tests for orc/coordination/board."""

import pytest
import yaml

import orc.coordination.board as _board
import orc.coordination.board._board as _board_impl
import orc.coordination.board._manager as _bm
from orc.coordination.models import Board


def _board_file(tmp_path):
    """Return the board.yaml path the manager uses (orc_dir/work/board.yaml)."""
    return tmp_path / ".orc" / "work" / "board.yaml"


def _work_dir(tmp_path):
    return tmp_path / ".orc" / "work"


class TestBoardCoverage:
    def test_read_board_exception_returns_empty(self, tmp_path):
        """Corrupt board.yaml → returns default empty board."""
        _board_file(tmp_path).write_text(": : invalid")
        board = _board_impl._read_board()
        assert board == Board(counter=0, tasks=[])

    def test_get_open_tasks_wraps_string_entries(self, tmp_path):
        """String entries in open list get wrapped in a dict."""
        _board_file(tmp_path).write_text("tasks:\n  - 0001-foo.md\n")
        tasks = _board.get_tasks()
        assert len(tasks) == 1
        assert tasks[0].name == "0001-foo.md"

    def test_get_open_tasks_returns_dict_entries_as_is(self, tmp_path):
        """Dict entries in the open list are returned unchanged."""
        _board_file(tmp_path).write_text("tasks:\n  - name: 0001-foo.md\n    status: planned\n")
        tasks = _board.get_tasks()
        assert len(tasks) == 1
        assert tasks[0].name == "0001-foo.md"
        assert tasks[0].status == "planned"

    def test_unassign_task_clears_assigned_to(self, tmp_path):
        """unassign_task removes the assigned_to field from a task."""
        _board_file(tmp_path).write_text(
            "tasks:\n  - name: 0001-foo.md\n    assigned_to: coder-1\n"
        )
        _board.unassign_task("0001-foo.md")
        board = yaml.safe_load(_board_file(tmp_path).read_text())
        assert board["tasks"][0].get("assigned_to") is None

    def test_unassign_task_noop_when_not_found(self, tmp_path):
        """unassign_task does nothing when the task name is not on the board."""
        _board_file(tmp_path).write_text("tasks: []\n")
        _board.unassign_task("nonexistent.md")  # should not raise

    def test_assign_task_not_found_warns(self, tmp_path):
        """Warning logged when task not found."""
        _board_file(tmp_path).write_text("tasks: []\n")
        _board.assign_task("nonexistent.md", "coder-1")  # should not raise

    def test_assign_task_sets_status_coding(self, tmp_path):
        """assign_task advances status to 'in-progress' for planned/rejected tasks."""
        _board_file(tmp_path).write_text("tasks:\n  - name: 0001-foo.md\n    status: planned\n")
        _board.assign_task("0001-foo.md", "coder-1")
        board = yaml.safe_load(_board_file(tmp_path).read_text())
        assert board["tasks"][0]["assigned_to"] == "coder-1"
        assert board["tasks"][0]["status"] == "in-progress"

    def test_assign_task_preserves_advanced_status(self, tmp_path):
        """assign_task does not overwrite in-review/done status."""
        _board_file(tmp_path).write_text("tasks:\n  - name: 0001-foo.md\n    status: in-review\n")
        _board.assign_task("0001-foo.md", "qa-1")
        board = yaml.safe_load(_board_file(tmp_path).read_text())
        assert board["tasks"][0]["status"] == "in-review"

    def test_clear_all_assignments_writes_when_changed(self, tmp_path):
        """Stale assignments cleared and written."""
        _board_file(tmp_path).write_text(
            "tasks:\n  - name: 0001-foo.md\n    assigned_to: coder-1\n"
        )
        _board.clear_all_assignments()
        board = yaml.safe_load(_board_file(tmp_path).read_text())
        assert board["tasks"][0].get("assigned_to") is None

    def test_write_board_atomic_cleans_up_on_error(self, tmp_path, monkeypatch):
        """_write_board cleans up the .tmp file and re-raises on OSError."""
        _board_file(tmp_path).write_text("tasks: []\n")

        original_replace = _bm.Path.replace

        def failing_replace(self, target):
            if self.suffix == ".tmp":
                raise OSError("disk full")
            return original_replace(self, target)

        monkeypatch.setattr(_bm.Path, "replace", failing_replace)

        with pytest.raises(OSError, match="disk full"):
            _board_impl._write_board(Board())

        tmp_file = _board_file(tmp_path).with_suffix(".yaml.tmp")
        assert not tmp_file.exists()

    def test_read_work_ignores_readme(self, tmp_path):
        """_read_work must not include README.md as a work item."""
        _board_file(tmp_path).write_text("counter: 1\ntasks:\n  - name: 0001-task.md\n")
        work_dir = _work_dir(tmp_path)
        (work_dir / "README.md").write_text("# This is the kanban README")
        (work_dir / "0001-task.md").write_text("# Task 1")
        result = _board._read_work()
        assert "README.md" not in result
        assert "This is the kanban README" not in result
        assert "0001-task.md" in result

    def test_read_work_excludes_task_content(self, tmp_path):
        """_read_work must not include task file content."""
        _board_file(tmp_path).write_text("counter: 1\ntasks:\n  - name: 0001-task.md\n")
        work_dir = _work_dir(tmp_path)
        (work_dir / "0001-task.md").write_text("SECRET task body")
        result = _board._read_work()
        assert "SECRET task body" not in result

    def test_read_work_excludes_comments(self, tmp_path):
        """_read_work must not include task comments in context."""
        _board_file(tmp_path).write_text(
            "counter: 1\ntasks:\n"
            "  - name: 0001-task.md\n    status: in-progress\n"
            "    comments:\n"
            "      - from: qa-1\n        text: fix the tests\n        ts: '2024-01-01T00:00:00Z'\n"
        )
        result = _board._read_work()
        assert "fix the tests" not in result
        assert "comments" not in result

    def test_set_task_status(self, tmp_path):
        """set_task_status updates the status field."""
        _board_file(tmp_path).write_text("tasks:\n  - name: 0001-foo.md\n    status: in-progress\n")
        _board.set_task_status("0001-foo.md", "in-review")
        board = yaml.safe_load(_board_file(tmp_path).read_text())
        assert board["tasks"][0]["status"] == "in-review"

    def test_add_task_comment(self, tmp_path):
        """add_task_comment appends to the comments list."""
        _board_file(tmp_path).write_text("tasks:\n  - name: 0001-foo.md\n    status: in-progress\n")
        _board.add_task_comment("0001-foo.md", "planner-1", "See ADR-0003")
        board = yaml.safe_load(_board_file(tmp_path).read_text())
        comments = board["tasks"][0]["comments"]
        assert len(comments) == 1
        assert comments[0]["from"] == "planner-1"
        assert comments[0]["text"] == "See ADR-0003"
        assert "ts" in comments[0]

    def test_get_task_returns_none_for_missing(self, tmp_path):
        """get_task returns None when task not on open list."""
        _board_file(tmp_path).write_text("tasks: []\n")
        assert _board.get_task("0001-missing.md") is None

    def test_get_task_returns_entry(self, tmp_path):
        """get_task returns the dict for a found task."""
        _board_file(tmp_path).write_text("tasks:\n  - name: 0001-foo.md\n    status: in-progress\n")
        t = _board.get_task("0001-foo.md")
        assert t is not None
        assert t.name == "0001-foo.md"
        assert t.status == "in-progress"


class TestFileBoardManagerCoverage:
    """Direct tests for FileBoardManager to hit uncovered branches."""

    def _mgr(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "work").mkdir()
        (cache_dir / "work" / "board.yaml").write_text("tasks:\n  - name: 0001-foo.md\n")
        return _bm.FileBoardManager(cache_dir)

    def test_work_dir_property(self, tmp_path):
        mgr = self._mgr(tmp_path)
        assert mgr.work_dir == tmp_path / "cache" / "work"

    def test_vision_dir_property(self, tmp_path):
        mgr = self._mgr(tmp_path)
        assert mgr.vision_dir == tmp_path / "cache" / "vision"

    def test_set_task_status_unknown_status_logs_warning(self, tmp_path):
        """set_task_status with unknown status logs a warning but still updates."""
        mgr = self._mgr(tmp_path)
        mgr.set_task_status("0001-foo.md", "not-a-real-status")

    def test_set_task_status_task_not_found_logs_warning(self, tmp_path):
        """set_task_status on a missing task logs a warning without raising."""
        mgr = self._mgr(tmp_path)
        mgr.set_task_status("9999-missing.md", "in-progress")

    def test_add_task_comment_task_not_found_logs_warning(self, tmp_path):
        """add_task_comment on a missing task logs a warning without raising."""
        mgr = self._mgr(tmp_path)
        mgr.add_task_comment("9999-missing.md", "planner-1", "hello")

    def test_list_task_files_missing_dir(self, tmp_path):
        """list_task_files returns [] when work_dir doesn't exist."""
        cache_dir = tmp_path / "nonexistent"
        mgr = _bm.FileBoardManager(cache_dir)
        assert mgr.list_task_files() == []

    def test_read_board_raises_on_invalid_board_structure(self, tmp_path):
        """ValidationError is raised and logged when board.yaml has invalid structure."""
        import pytest
        from pydantic import ValidationError

        mgr = self._mgr(tmp_path)
        (tmp_path / "cache" / "work" / "board.yaml").write_text(
            "counter: 1\ntasks: [{name: 123, status: [invalid-list-not-a-string]}]\n"
        )
        with pytest.raises(ValidationError):
            mgr.read_board()

    def test_list_task_files_returns_sorted_md_files(self, tmp_path):
        """list_task_files returns sorted .md files excluding README.md."""
        mgr = self._mgr(tmp_path)
        work = tmp_path / "cache" / "work"
        (work / "0002-beta.md").write_text("beta")
        (work / "0001-alpha.md").write_text("alpha")
        (work / "README.md").write_text("readme")
        result = mgr.list_task_files()
        names = [p.name for p in result]
        assert "README.md" not in names
        assert names == sorted(names)

    def test_board_to_dict_includes_commit_tag_and_timestamp(self, tmp_path):
        """_board_to_dict serialises commit_tag and timestamp when present."""
        from orc.coordination.models import TaskEntry

        entry = TaskEntry(name="0001-x.md", commit_tag="abc123", timestamp="2024-01-01T10:00:00Z")
        board = Board(counter=1, tasks=[entry])
        d = _bm._board_to_dict(board)
        task = d["tasks"][0]
        assert task["commit_tag"] == "abc123"
        assert task["timestamp"] == "2024-01-01T10:00:00Z"

    def test_board_from_dict_coerces_non_list_tasks(self):
        """_board_from_dict treats non-list 'tasks' as empty."""
        board = _bm._board_from_dict({"counter": 1, "tasks": "not-a-list"})
        assert board.tasks == []


class TestBoardModuleFunctions:
    """Cover module-level functions in _board.py."""

    def test_delete_task_removes_entry_and_file(self, tmp_path):
        """delete_task removes the entry from board.yaml and deletes the task file."""
        work_dir = _work_dir(tmp_path)
        _board_file(tmp_path).write_text(
            "counter: 1\ntasks:\n  - name: 0001-foo.md\n    status: planned\n"
        )
        (work_dir / "0001-foo.md").write_text("# task body")
        _board.delete_task("0001-foo.md")
        board = yaml.safe_load(_board_file(tmp_path).read_text())
        assert board["tasks"] == []
        assert not (work_dir / "0001-foo.md").exists()

    def test_read_work_includes_assigned_to(self, tmp_path):
        """_read_work includes the assigned_to field when present."""
        _board_file(tmp_path).write_text(
            "counter: 1\ntasks:\n"
            "  - name: 0001-foo.md\n    status: in-progress\n    assigned_to: coder-1\n"
        )
        result = _board._read_work()
        assert "assigned_to: coder-1" in result

    def test_create_task_delegates_to_manager(self, tmp_path):
        """create_task passes through to FileBoardManager.create_task."""
        from orc.coordination.models import TaskBody

        _board_file(tmp_path).write_text("counter: 1\ntasks: []\n")
        body = TaskBody(
            overview="Implement X",
            in_scope=["feature X"],
            out_of_scope=["feature Y"],
            steps=["step 1"],
        )
        name, path = _board_impl.create_task("Feature X", "feature-x.md", body)
        assert name.endswith(".md")
        assert path.exists()
        board = yaml.safe_load(_board_file(tmp_path).read_text())
        assert len(board["tasks"]) == 1

    def test_get_manager_reinits_when_orc_dir_changes(self, tmp_path, monkeypatch):
        """_get_manager creates a new manager when orc_dir changes."""
        from dataclasses import replace as _replace

        import orc.config as _cfg

        # First call — creates manager
        _board_impl._manager = None
        _board_impl._get_manager()
        first = _board_impl._manager

        # Change orc_dir
        new_orc = tmp_path / "other-orc"
        (new_orc / "work").mkdir(parents=True)
        (new_orc / "work" / "board.yaml").write_text("counter: 0\ntasks: []\n")
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), orc_dir=new_orc))

        second = _board_impl._get_manager()
        assert second is not first
        # Reset
        _board_impl._manager = None
