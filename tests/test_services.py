"""Tests for orc/services.py — service protocol types."""

from __future__ import annotations

from pathlib import Path

import pytest

from orc.engine.services import BoardService, MessagingService, WorktreeService

# ---------------------------------------------------------------------------
# Concrete stub implementations that satisfy each protocol
# ---------------------------------------------------------------------------


class StubBoard:
    def get_tasks(self) -> list[dict]:
        return []

    def assign_task(self, task_name: str, agent_id: str) -> None:
        pass

    def unassign_task(self, task_name: str) -> None:
        pass

    def get_pending_visions(self) -> list[str]:
        return []

    def get_pending_reviews(self) -> list[str]:
        return []

    def get_blocked_tasks(self) -> list[str]:
        return []

    def scan_todos(self) -> list[dict]:
        return []

    def is_empty(self) -> bool:
        return True

    def query_tasks(self, status: str) -> list[str]:
        return []

    def delete_task(self, task_name: str) -> None:
        pass


class StubWorktree:
    def ensure_feature_worktree(self, task_name: str) -> Path:
        return Path("/tmp/stub-wt")

    def ensure_dev_worktree(self) -> Path:
        return Path("/tmp/stub-dev-wt")


class StubMessaging:
    def get_messages(self) -> list[dict]:
        return []

    def post_boot_message(self, agent_id: str, body: str) -> None:
        pass


# ---------------------------------------------------------------------------
# Protocol conformance tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stub,protocol",
    [
        (StubBoard(), BoardService),
        (StubWorktree(), WorktreeService),
        (StubMessaging(), MessagingService),
    ],
)
def test_stub_satisfies_protocol(stub, protocol):
    assert isinstance(stub, protocol)


class TestBoardServiceProtocol:
    def test_incomplete_class_does_not_satisfy_protocol(self):
        class Incomplete:
            def get_tasks(self):
                return []

            # missing all other methods

        # runtime_checkable only checks for method existence (structural check)
        # A class with only one method does NOT satisfy the protocol because the
        # remaining methods are absent.
        assert not isinstance(Incomplete(), BoardService)


class TestWorktreeServiceProtocol:
    def test_class_missing_method_fails(self):
        class Partial:
            def ensure_dev_worktree(self) -> Path:
                return Path("/tmp")

            # missing ensure_feature_worktree

        assert not isinstance(Partial(), WorktreeService)


class TestMessagingServiceProtocol:
    def test_empty_class_fails(self):
        assert not isinstance(object(), MessagingService)
