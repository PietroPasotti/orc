"""Tests for orc/board.py."""

import yaml

import orc.board as _board
import orc.config as _cfg


class TestBoardCoverage:
    def test_read_board_exception_returns_empty(self, tmp_path, monkeypatch):
        """Lines 35-36: corrupt board.yaml → returns default empty board."""
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path / ".orc")
        monkeypatch.setattr(_cfg, "WORK_DIR", tmp_path / ".orc" / "work")
        monkeypatch.setattr(_cfg, "BOARD_FILE", tmp_path / ".orc" / "work" / "board.yaml")
        (tmp_path / ".orc" / "work").mkdir(parents=True)
        (tmp_path / ".orc" / "work" / "board.yaml").write_text(": : invalid")
        board = _board._read_board()
        assert board == {"counter": 0, "open": [], "done": []}

    def test_get_open_tasks_wraps_string_entries(self, tmp_path, monkeypatch):
        """Line 53: string entries in open list get wrapped in a dict."""
        monkeypatch.setattr(_cfg, "BOARD_FILE", tmp_path / "board.yaml")
        (tmp_path / "board.yaml").write_text("open:\n  - 0001-foo.md\ndone: []\n")
        tasks = _board.get_open_tasks()
        assert tasks == [{"name": "0001-foo.md"}]

    def test_assign_task_not_found_warns(self, tmp_path, monkeypatch):
        """Line 66: warning logged when task not found."""
        monkeypatch.setattr(_cfg, "BOARD_FILE", tmp_path / "board.yaml")
        (tmp_path / "board.yaml").write_text("open: []\ndone: []\n")
        _board.assign_task("nonexistent.md", "coder-1")  # should not raise

    def test_clear_all_assignments_writes_when_changed(self, tmp_path, monkeypatch):
        """Lines 89, 91-92: stale assignments cleared and written."""
        monkeypatch.setattr(_cfg, "BOARD_FILE", tmp_path / "board.yaml")
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path / ".orc")
        monkeypatch.setattr(_cfg, "WORK_DIR", tmp_path / ".orc" / "work")
        (tmp_path / "board.yaml").write_text(
            "open:\n  - name: 0001-foo.md\n    assigned_to: coder-1\ndone: []\n"
        )
        _board.clear_all_assignments()
        board = yaml.safe_load((tmp_path / "board.yaml").read_text())
        assert board["open"][0].get("assigned_to") is None

    def test_active_task_name_returns_none_for_empty_board(self, tmp_path, monkeypatch):
        """Line 107: has_open_work returns False when board empty."""
        monkeypatch.setattr(_cfg, "BOARD_FILE", tmp_path / "board.yaml")
        (tmp_path / "board.yaml").write_text("open: []\ndone: []\n")
        assert _board.has_open_work() is False
