"""Tests for orc/board.py and orc/board_manager.py."""

import pytest
import yaml

import orc.board as _board
import orc.board_manager as _bm


def _board_file(tmp_path):
    """Return the board.yaml path the manager uses (orc_dir/work/board.yaml)."""
    return tmp_path / ".orc" / "work" / "board.yaml"


def _work_dir(tmp_path):
    return tmp_path / ".orc" / "work"


class TestBoardCoverage:
    def test_read_board_exception_returns_empty(self, tmp_path):
        """Corrupt board.yaml → returns default empty board."""
        _board_file(tmp_path).write_text(": : invalid")
        board = _board._read_board()
        assert board == {"counter": 0, "open": [], "done": []}

    def test_get_open_tasks_wraps_string_entries(self, tmp_path):
        """String entries in open list get wrapped in a dict."""
        _board_file(tmp_path).write_text("open:\n  - 0001-foo.md\ndone: []\n")
        tasks = _board.get_open_tasks()
        assert tasks == [{"name": "0001-foo.md"}]

    def test_assign_task_not_found_warns(self, tmp_path):
        """Warning logged when task not found."""
        _board_file(tmp_path).write_text("open: []\ndone: []\n")
        _board.assign_task("nonexistent.md", "coder-1")  # should not raise

    def test_assign_task_sets_status_coding(self, tmp_path):
        """assign_task advances status to 'coding' for planned/rejected tasks."""
        _board_file(tmp_path).write_text(
            "open:\n  - name: 0001-foo.md\n    status: planned\ndone: []\n"
        )
        _board.assign_task("0001-foo.md", "coder-1")
        board = yaml.safe_load(_board_file(tmp_path).read_text())
        assert board["open"][0]["assigned_to"] == "coder-1"
        assert board["open"][0]["status"] == "coding"

    def test_assign_task_preserves_advanced_status(self, tmp_path):
        """assign_task does not overwrite review/approved status."""
        _board_file(tmp_path).write_text(
            "open:\n  - name: 0001-foo.md\n    status: review\ndone: []\n"
        )
        _board.assign_task("0001-foo.md", "qa-1")
        board = yaml.safe_load(_board_file(tmp_path).read_text())
        assert board["open"][0]["status"] == "review"

    def test_clear_all_assignments_writes_when_changed(self, tmp_path):
        """Stale assignments cleared and written."""
        _board_file(tmp_path).write_text(
            "open:\n  - name: 0001-foo.md\n    assigned_to: coder-1\ndone: []\n"
        )
        _board.clear_all_assignments()
        board = yaml.safe_load(_board_file(tmp_path).read_text())
        assert board["open"][0].get("assigned_to") is None

    def test_active_task_name_returns_none_for_empty_board(self, tmp_path):
        """_active_task_name returns None when board is empty."""
        _board_file(tmp_path).write_text("open: []\ndone: []\n")
        assert _board._active_task_name() is None

    def test_write_board_atomic_cleans_up_on_error(self, tmp_path, monkeypatch):
        """_write_board cleans up the .tmp file and re-raises on OSError."""
        _board_file(tmp_path).write_text("open: []\ndone: []\n")

        original_replace = _bm.Path.replace

        def failing_replace(self, target):
            if self.suffix == ".tmp":
                raise OSError("disk full")
            return original_replace(self, target)

        monkeypatch.setattr(_bm.Path, "replace", failing_replace)

        with pytest.raises(OSError, match="disk full"):
            _board._write_board({"open": [], "done": []})

        tmp_file = _board_file(tmp_path).with_suffix(".yaml.tmp")
        assert not tmp_file.exists()

    def test_read_work_ignores_readme(self, tmp_path):
        """_read_work must not include README.md as a work item."""
        work_dir = _work_dir(tmp_path)
        _board_file(tmp_path).write_text("counter: 1\nopen:\n  - name: 0001-task.md\ndone: []\n")
        (work_dir / "README.md").write_text("# This is the kanban README")
        (work_dir / "0001-task.md").write_text("# Task 1")
        result = _board._read_work()
        assert "README.md" not in result
        assert "This is the kanban README" not in result
        assert "0001-task.md" in result

    def test_set_task_status(self, tmp_path):
        """set_task_status updates the status field."""
        _board_file(tmp_path).write_text(
            "open:\n  - name: 0001-foo.md\n    status: coding\ndone: []\n"
        )
        _board.set_task_status("0001-foo.md", "review")
        board = yaml.safe_load(_board_file(tmp_path).read_text())
        assert board["open"][0]["status"] == "review"

    def test_add_task_comment(self, tmp_path):
        """add_task_comment appends to the comments list."""
        _board_file(tmp_path).write_text(
            "open:\n  - name: 0001-foo.md\n    status: coding\ndone: []\n"
        )
        _board.add_task_comment("0001-foo.md", "planner-1", "See ADR-0003")
        board = yaml.safe_load(_board_file(tmp_path).read_text())
        comments = board["open"][0]["comments"]
        assert len(comments) == 1
        assert comments[0]["from"] == "planner-1"
        assert comments[0]["text"] == "See ADR-0003"
        assert "ts" in comments[0]

    def test_get_task_returns_none_for_missing(self, tmp_path):
        """get_task returns None when task not on open list."""
        _board_file(tmp_path).write_text("open: []\ndone: []\n")
        assert _board.get_task("0001-missing.md") is None

    def test_get_task_returns_entry(self, tmp_path):
        """get_task returns the dict for a found task."""
        _board_file(tmp_path).write_text(
            "open:\n  - name: 0001-foo.md\n    status: coding\ndone: []\n"
        )
        t = _board.get_task("0001-foo.md")
        assert t is not None
        assert t["name"] == "0001-foo.md"
        assert t["status"] == "coding"


class TestFileBoardManagerCoverage:
    """Direct tests for FileBoardManager to hit uncovered branches."""

    def _mgr(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "work").mkdir()
        (cache_dir / "work" / "board.yaml").write_text("open:\n  - name: 0001-foo.md\ndone: []\n")
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
        mgr.set_task_status("9999-missing.md", "coding")

    def test_add_task_comment_task_not_found_logs_warning(self, tmp_path):
        """add_task_comment on a missing task logs a warning without raising."""
        mgr = self._mgr(tmp_path)
        mgr.add_task_comment("9999-missing.md", "planner-1", "hello")

    def test_list_task_files_missing_dir(self, tmp_path):
        """list_task_files returns [] when work_dir doesn't exist."""
        cache_dir = tmp_path / "nonexistent"
        mgr = _bm.FileBoardManager(cache_dir)
        assert mgr.list_task_files() == []
