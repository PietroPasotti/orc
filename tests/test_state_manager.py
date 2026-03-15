"""Tests for orc.coordination.state.BoardStateManager."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# ── helpers ──────────────────────────────────────────────────────────────────


def _orc_dir(tmp_path: Path) -> Path:
    orc = tmp_path / ".orc"
    orc.mkdir(exist_ok=True)
    (orc / "work").mkdir(exist_ok=True)
    (orc / "vision" / "ready").mkdir(parents=True, exist_ok=True)
    (orc / "work" / "board.yaml").write_text("counter: 0\ntasks: []\n")
    return orc


def _state(orc_dir: Path):
    """Return a BoardStateManager rooted at *orc_dir* (already set up by _orc_dir)."""
    from orc.coordination.state import BoardStateManager

    return BoardStateManager(orc_dir)


_VISION = "0001-test-vision.md"


def _task_body(**overrides) -> dict:
    """Return a minimal valid task body dict, with optional overrides."""
    base = {
        "overview": "Implement the feature.",
        "in_scope": ["core logic"],
        "out_of_scope": ["UI changes"],
        "steps": ["Write tests", "Implement"],
        "notes": "",
    }
    base.update(overrides)
    return base


class TestStateManagerBoardQueries:
    def test_get_open_tasks_empty(self, tmp_path):
        orc = _orc_dir(tmp_path)
        assert _state(orc).get_tasks() == []

    def test_get_open_tasks_wraps_strings(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "work" / "board.yaml").write_text("tasks:\n  - 0001-foo.md\n")
        result = _state(orc).get_tasks()
        assert result == [{"name": "0001-foo.md"}]

    def test_get_open_tasks_returns_dicts(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "work" / "board.yaml").write_text(
            "tasks:\n  - name: 0001-foo.md\n    status: in-progress\n"
        )
        result = _state(orc).get_tasks()
        assert result[0]["name"] == "0001-foo.md"
        assert result[0]["status"] == "in-progress"

    def test_get_task_found(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "work" / "board.yaml").write_text(
            "tasks:\n  - name: 0001-foo.md\n    status: planned\n"
        )
        t = _state(orc).get_task("0001-foo.md")
        assert t is not None
        assert t["name"] == "0001-foo.md"

    def test_get_task_not_found(self, tmp_path):
        orc = _orc_dir(tmp_path)
        assert _state(orc).get_task("9999-missing.md") is None


class TestStateManagerBoardMutations:
    def test_create_task_creates_file_and_board_entry(self, tmp_path):
        orc = _orc_dir(tmp_path)
        s = _state(orc)
        filename, path = s.create_task("add-user-auth", _VISION, _task_body())
        assert filename == "0000-add-user-auth.md"
        assert path.exists()
        board = yaml.safe_load((orc / "work" / "board.yaml").read_text())
        assert board["counter"] == 1
        assert board["tasks"][0]["name"] == filename
        assert board["tasks"][0]["status"] == "planned"

    def test_create_task_increments_counter(self, tmp_path):
        orc = _orc_dir(tmp_path)
        s = _state(orc)
        f1, _ = s.create_task("first", _VISION, _task_body())
        f2, _ = s.create_task("second", _VISION, _task_body())
        assert f1.startswith("0000-")
        assert f2.startswith("0001-")

    def test_set_task_status(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "work" / "board.yaml").write_text(
            "tasks:\n  - name: 0001-foo.md\n    status: planned\n"
        )
        _state(orc).set_task_status("0001-foo.md", "in-review")
        board = yaml.safe_load((orc / "work" / "board.yaml").read_text())
        assert board["tasks"][0]["status"] == "in-review"

    def test_assign_task_sets_assigned_to(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "work" / "board.yaml").write_text(
            "tasks:\n  - name: 0001-foo.md\n    status: planned\n"
        )
        _state(orc).assign_task("0001-foo.md", "coder-1")
        board = yaml.safe_load((orc / "work" / "board.yaml").read_text())
        assert board["tasks"][0]["assigned_to"] == "coder-1"
        assert board["tasks"][0]["status"] == "in-progress"

    def test_assign_task_preserves_advanced_status(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "work" / "board.yaml").write_text(
            "tasks:\n  - name: 0001-foo.md\n    status: in-review\n"
        )
        _state(orc).assign_task("0001-foo.md", "qa-1")
        board = yaml.safe_load((orc / "work" / "board.yaml").read_text())
        assert board["tasks"][0]["status"] == "in-review"

    def test_assign_task_not_found_warns(self, tmp_path):
        orc = _orc_dir(tmp_path)
        _state(orc).assign_task("9999-missing.md", "coder-1")  # should not raise

    def test_unassign_task(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "work" / "board.yaml").write_text(
            "tasks:\n  - name: 0001-foo.md\n    assigned_to: coder-1\n"
        )
        _state(orc).unassign_task("0001-foo.md")
        board = yaml.safe_load((orc / "work" / "board.yaml").read_text())
        assert board["tasks"][0].get("assigned_to") is None

    def test_unassign_task_not_found_no_write(self, tmp_path):
        orc = _orc_dir(tmp_path)
        _state(orc).unassign_task("9999-missing.md")  # should not raise

    def test_clear_all_assignments(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "work" / "board.yaml").write_text(
            "tasks:\n  - name: 0001-foo.md\n    assigned_to: coder-1\n"
        )
        _state(orc).clear_all_assignments()
        board = yaml.safe_load((orc / "work" / "board.yaml").read_text())
        assert board["tasks"][0].get("assigned_to") is None

    def test_clear_all_assignments_no_change_skips_write(self, tmp_path):
        # No assigned_to fields → no write needed (should not raise).
        orc = _orc_dir(tmp_path)
        _state(orc).clear_all_assignments()

    def test_add_task_comment(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "work" / "board.yaml").write_text(
            "tasks:\n  - name: 0001-foo.md\n    status: in-review\n"
        )
        _state(orc).add_task_comment("0001-foo.md", "qa-1", "Missing tests")
        board = yaml.safe_load((orc / "work" / "board.yaml").read_text())
        comments = board["tasks"][0]["comments"]
        assert len(comments) == 1
        assert comments[0]["from"] == "qa-1"
        assert comments[0]["text"] == "Missing tests"


class TestStateManagerVisions:
    def test_get_pending_visions_empty(self, tmp_path):
        orc = _orc_dir(tmp_path)
        assert _state(orc).get_pending_visions() == []

    def test_get_pending_visions_no_vision_dir(self, tmp_path):
        orc = tmp_path / ".orc"
        orc.mkdir(exist_ok=True)
        (orc / "work").mkdir(exist_ok=True)
        (orc / "work" / "board.yaml").write_text("tasks: []\n")
        from orc.coordination.state import BoardStateManager

        assert BoardStateManager(orc).get_pending_visions() == []

    def test_get_pending_visions_with_file(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "vision" / "ready" / "0001-feature.md").write_text("# Vision")
        visions = _state(orc).get_pending_visions()
        assert visions == ["0001-feature.md"]

    def test_get_pending_visions_skips_dotfiles(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "vision" / "ready" / ".future-work.md").write_text("# Future")
        assert _state(orc).get_pending_visions() == []

    def test_get_pending_visions_skips_readme(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "vision" / "ready" / "README.md").write_text("# README")
        assert _state(orc).get_pending_visions() == []

    def test_get_pending_visions_skips_matched_tasks(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "vision" / "ready" / "0001-feature.md").write_text("# Vision")
        (orc / "work" / "board.yaml").write_text(
            "tasks:\n  - name: 0001-feature.md\n    status: planned\n"
        )
        assert _state(orc).get_pending_visions() == []

    def test_get_pending_visions_skips_stem_matched_tasks(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "vision" / "ready" / "0001-feature.md").write_text("# Vision")
        (orc / "work" / "board.yaml").write_text("tasks:\n  - name: 0001-feature.md\n")
        assert _state(orc).get_pending_visions() == []

    def test_read_vision_found(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "vision" / "ready" / "0001-feature.md").write_text("# Feature Vision")
        content = _state(orc).read_vision("0001-feature.md")
        assert "Feature Vision" in content

    def test_read_vision_not_found(self, tmp_path):
        orc = _orc_dir(tmp_path)
        with pytest.raises(FileNotFoundError, match="Vision not found"):
            _state(orc).read_vision("9999-missing.md")

    def test_close_vision_moves_file_to_done_subdir(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "vision" / "ready" / "0001-feature.md").write_text("# Feature")
        _state(orc).close_vision("0001-feature.md", "Built the feature.", ["0001-task.md"])
        assert not (orc / "vision" / "ready" / "0001-feature.md").exists()
        assert (orc / "vision" / "done" / "0001-feature.md").exists()

    def test_close_vision_does_not_write_changelog(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "vision" / "ready" / "0001-feature.md").write_text("# Feature")
        _state(orc).close_vision("0001-feature.md", "Built the feature.", ["0001-task.md"])
        assert not (orc / "orc-CHANGELOG.md").exists()

    def test_close_vision_not_found_raises(self, tmp_path):
        orc = _orc_dir(tmp_path)
        with pytest.raises(FileNotFoundError, match="Vision not found"):
            _state(orc).close_vision("9999-missing.md", "nope", [])


# ─────────────────────────────────────────────────────────────────────────────
# Route handler tests (direct function calls, no HTTP transport)
# ─────────────────────────────────────────────────────────────────────────────
