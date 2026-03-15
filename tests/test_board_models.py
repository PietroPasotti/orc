"""Tests for orc/board_models.py pydantic validation."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from orc.board_models import Board, DoneTaskEntry, TaskEntry


class TestTaskEntry:
    def test_valid_minimal(self):
        entry = TaskEntry(name="0001-foo.md", status="planned")
        assert entry.name == "0001-foo.md"
        assert entry.status == "planned"
        assert entry.assigned_to is None
        assert entry.comments == []

    def test_valid_full(self):
        entry = TaskEntry(
            name="0001-foo.md",
            status="coding",
            assigned_to="coder-1",
            comments=[{"from": "planner", "text": "note", "ts": "2026-01-01T00:00:00Z"}],
        )
        assert entry.assigned_to == "coder-1"
        assert len(entry.comments) == 1

    def test_missing_name_raises(self):
        with pytest.raises(ValidationError):
            TaskEntry(status="planned")

    def test_missing_status_defaults_to_empty(self):
        entry = TaskEntry(name="0001-foo.md")
        assert entry.status == ""

    def test_string_entry_coerced_to_task_entry(self):
        entry = TaskEntry.model_validate("0001-foo.md")
        assert entry.name == "0001-foo.md"

    def test_extra_fields_ignored(self):
        entry = TaskEntry(name="0001-foo.md", status="planned", unknown_field="x")
        assert not hasattr(entry, "unknown_field")


class TestDoneTaskEntry:
    def test_valid_via_alias(self):
        entry = DoneTaskEntry(
            **{"name": "0001-foo.md", "commit-tag": "abc123", "timestamp": "2026-01-01T00:00:00Z"}
        )
        assert entry.commit_tag == "abc123"

    def test_valid_via_python_name(self):
        entry = DoneTaskEntry(
            name="0001-foo.md", commit_tag="abc123", timestamp="2026-01-01T00:00:00Z"
        )
        assert entry.commit_tag == "abc123"

    def test_missing_commit_tag_defaults_to_none(self):
        entry = DoneTaskEntry(name="0001-foo.md", timestamp="2026-01-01T00:00:00Z")
        assert entry.commit_tag is None

    def test_missing_timestamp_defaults_to_none(self):
        entry = DoneTaskEntry(**{"name": "0001-foo.md", "commit-tag": "abc123"})
        assert entry.timestamp is None

    def test_string_entry_coerced_to_done_entry(self):
        entry = DoneTaskEntry.model_validate("0001-done.md")
        assert entry.name == "0001-done.md"

    def test_datetime_timestamp_coerced_to_string(self):
        ts = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        entry = DoneTaskEntry(**{"name": "0001-foo.md", "commit-tag": "abc", "timestamp": ts})
        assert isinstance(entry.timestamp, str)
        assert "2026" in entry.timestamp

    def test_model_dump_uses_alias(self):
        entry = DoneTaskEntry(
            **{"name": "0001-foo.md", "commit-tag": "abc123", "timestamp": "2026-01-01T00:00:00Z"}
        )
        dumped = entry.model_dump(by_alias=True)
        assert "commit-tag" in dumped
        assert dumped["commit-tag"] == "abc123"


class TestBoard:
    def test_valid_empty(self):
        board = Board.model_validate({"counter": 0, "open": [], "done": []})
        assert board.counter == 0
        assert board.open == []
        assert board.done == []

    def test_valid_with_tasks(self):
        data = {
            "counter": 5,
            "open": [{"name": "0005-task.md", "status": "planned"}],
            "done": [
                {
                    "name": "0001-done.md",
                    "commit-tag": "abc123",
                    "timestamp": "2026-01-01T00:00:00Z",
                }
            ],
        }
        board = Board.model_validate(data)
        assert board.counter == 5
        assert len(board.open) == 1
        assert len(board.done) == 1
        assert board.open[0].name == "0005-task.md"
        assert board.done[0].commit_tag == "abc123"

    def test_string_entries_in_open_coerced(self):
        data = {"counter": 0, "open": ["0001-foo.md"], "done": []}
        board = Board.model_validate(data)
        assert board.open[0].name == "0001-foo.md"

    def test_missing_required_field_in_open_task_raises(self):
        data = {
            "counter": 1,
            "open": [{"status": "planned"}],  # missing name
            "done": [],
        }
        with pytest.raises(ValidationError):
            Board.model_validate(data)

    def test_missing_required_field_in_done_raises(self):
        data = {
            "counter": 1,
            "open": [],
            # missing name
            "done": [{"commit-tag": "abc123", "timestamp": "2026-01-01T00:00:00Z"}],
        }
        with pytest.raises(ValidationError):
            Board.model_validate(data)

    def test_extra_fields_ignored(self):
        data = {"counter": 0, "open": [], "done": [], "future_field": "value"}
        board = Board.model_validate(data)
        assert not hasattr(board, "future_field")

    def test_model_dump_by_alias_preserves_commit_tag_key(self):
        data = {
            "counter": 1,
            "open": [],
            "done": [
                {
                    "name": "0001-done.md",
                    "commit-tag": "abc123",
                    "timestamp": "2026-01-01T00:00:00Z",
                }
            ],
        }
        board = Board.model_validate(data)
        dumped = board.model_dump(by_alias=True)
        assert dumped["done"][0]["commit-tag"] == "abc123"

    def test_defaults_when_fields_absent(self):
        board = Board.model_validate({})
        assert board.counter == 0
        assert board.open == []
        assert board.done == []
