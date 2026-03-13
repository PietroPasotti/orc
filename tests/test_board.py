"""Tests for orc/board.py."""

from dataclasses import replace as _replace

import yaml

import orc.board as _board
import orc.config as _cfg


class TestBoardCoverage:
    def test_read_board_exception_returns_empty(self, tmp_path, monkeypatch):
        """Lines 35-36: corrupt board.yaml → returns default empty board."""
        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(
                _cfg.get(),
                orc_dir=tmp_path / ".orc",
                repo_root=tmp_path,
                dev_worktree=tmp_path / "dev-wt",
                work_dir=tmp_path / ".orc" / "work",
                board_file=tmp_path / ".orc" / "work" / "board.yaml",
            ),
        )
        (tmp_path / ".orc" / "work").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".orc" / "work" / "board.yaml").write_text(": : invalid")
        board = _board._read_board()
        assert board == {"counter": 0, "open": [], "done": []}

    def test_get_open_tasks_wraps_string_entries(self, tmp_path, monkeypatch):
        """Line 53: string entries in open list get wrapped in a dict."""
        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(
                _cfg.get(),
                orc_dir=tmp_path / ".orc",
                repo_root=tmp_path,
                dev_worktree=tmp_path / "dev-wt",
                board_file=tmp_path / "board.yaml",
            ),
        )
        (tmp_path / "board.yaml").write_text("open:\n  - 0001-foo.md\ndone: []\n")
        tasks = _board.get_open_tasks()
        assert tasks == [{"name": "0001-foo.md"}]

    def test_assign_task_not_found_warns(self, tmp_path, monkeypatch):
        """Line 66: warning logged when task not found."""
        monkeypatch.setattr(
            _cfg, "_config", _replace(_cfg.get(), board_file=tmp_path / "board.yaml")
        )
        (tmp_path / "board.yaml").write_text("open: []\ndone: []\n")
        _board.assign_task("nonexistent.md", "coder-1")  # should not raise

    def test_clear_all_assignments_writes_when_changed(self, tmp_path, monkeypatch):
        """Lines 89, 91-92: stale assignments cleared and written."""
        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(
                _cfg.get(),
                orc_dir=tmp_path / ".orc",
                repo_root=tmp_path,
                dev_worktree=tmp_path / "dev-wt",
                board_file=tmp_path / "board.yaml",
                work_dir=tmp_path / ".orc" / "work",
            ),
        )
        (tmp_path / "board.yaml").write_text(
            "open:\n  - name: 0001-foo.md\n    assigned_to: coder-1\ndone: []\n"
        )
        _board.clear_all_assignments()
        board = yaml.safe_load((tmp_path / "board.yaml").read_text())
        assert board["open"][0].get("assigned_to") is None

    def test_active_task_name_returns_none_for_empty_board(self, tmp_path, monkeypatch):
        """_active_task_name returns None when board is empty."""
        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(
                _cfg.get(), board_file=tmp_path / "board.yaml", dev_worktree=tmp_path / "dev-wt"
            ),
        )
        (tmp_path / "board.yaml").write_text("open: []\ndone: []\n")
        assert _board._active_task_name() is None

    def test_write_board_atomic_cleans_up_on_error(self, tmp_path, monkeypatch):
        """_write_board cleans up the .tmp file and re-raises on OSError."""

        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(
                _cfg.get(),
                orc_dir=tmp_path / ".orc",
                repo_root=tmp_path,
                dev_worktree=tmp_path / "dev-wt",
                board_file=tmp_path / "board.yaml",
            ),
        )
        (tmp_path / "board.yaml").write_text("open: []\ndone: []\n")

        original_replace = _board.Path.replace

        def failing_replace(self, target):
            # Simulate a failure only for .tmp files
            if self.suffix == ".tmp":
                raise OSError("disk full")
            return original_replace(self, target)

        monkeypatch.setattr(_board.Path, "replace", failing_replace)

        import pytest as _pytest

        with _pytest.raises(OSError, match="disk full"):
            _board._write_board({"open": [], "done": []})

        # The .tmp file must have been cleaned up
        tmp_file = tmp_path / "board.yaml.tmp"
        assert not tmp_file.exists()

    def test_read_work_ignores_readme(self, tmp_path, monkeypatch):
        """_read_work must not include README.md as a work item."""
        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(
                _cfg.get(),
                orc_dir=tmp_path / ".orc",
                repo_root=tmp_path,
                dev_worktree=tmp_path / "dev-wt",
                work_dir=tmp_path / ".orc" / "work",
            ),
        )
        work_dir = tmp_path / ".orc" / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        board_file = work_dir / "board.yaml"
        board_file.write_text("counter: 1\nopen:\n  - name: 0001-task.md\ndone: []\n")
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), board_file=board_file))
        (work_dir / "README.md").write_text("# This is the kanban README")
        (work_dir / "0001-task.md").write_text("# Task 1")
        result = _board._read_work()
        assert "README.md" not in result
        assert "This is the kanban README" not in result
        assert "0001-task.md" in result
