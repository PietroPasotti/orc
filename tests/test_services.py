"""Tests for orc/services.py — service protocol types."""

from __future__ import annotations

from pathlib import Path

from orc.services import BoardService, MessagingService, WorktreeService

# ---------------------------------------------------------------------------
# Concrete stub implementations that satisfy each protocol
# ---------------------------------------------------------------------------


class StubBoard:
    def get_open_tasks(self) -> list[dict]:
        return []

    def assign_task(self, task_name: str, agent_id: str) -> None:
        pass

    def unassign_task(self, task_name: str) -> None:
        pass

    def get_pending_visions(self) -> list[str]:
        return []

    def get_pending_reviews(self) -> list[str]:
        return []


class StubWorktree:
    def ensure_feature_worktree(self, task_name: str) -> Path:
        return Path("/tmp/stub-wt")

    def ensure_dev_worktree(self) -> Path:
        return Path("/tmp/stub-dev-wt")


class StubMessaging:
    def get_messages(self) -> list[dict]:
        return []

    def has_unresolved_block(self, messages: list[dict]) -> tuple[str | None, str | None]:
        return None, None

    def wait_for_human_reply(self, messages: list[dict]) -> str:
        return "ok"

    def post_boot_message(self, agent_id: str, body: str) -> None:
        pass

    def post_resolved(self, blocked_agent: str, blocked_state: str, resolver: str) -> None:
        pass

    def boot_message_body(self) -> str:
        return "boot"


# ---------------------------------------------------------------------------
# Protocol conformance tests
# ---------------------------------------------------------------------------


class TestBoardServiceProtocol:
    def test_stub_satisfies_protocol(self):
        assert isinstance(StubBoard(), BoardService)

    def test_incomplete_class_does_not_satisfy_protocol(self):
        class Incomplete:
            def get_open_tasks(self):
                return []

            # missing all other methods

        # runtime_checkable only checks for method existence (structural check)
        # A class with only one method does NOT satisfy the protocol because the
        # remaining methods are absent.
        assert not isinstance(Incomplete(), BoardService)


class TestWorktreeServiceProtocol:
    def test_stub_satisfies_protocol(self):
        assert isinstance(StubWorktree(), WorktreeService)

    def test_class_missing_method_fails(self):
        class Partial:
            def ensure_dev_worktree(self) -> Path:
                return Path("/tmp")

            # missing ensure_feature_worktree

        assert not isinstance(Partial(), WorktreeService)


class TestMessagingServiceProtocol:
    def test_stub_satisfies_protocol(self):
        assert isinstance(StubMessaging(), MessagingService)

    def test_empty_class_fails(self):
        assert not isinstance(object(), MessagingService)
