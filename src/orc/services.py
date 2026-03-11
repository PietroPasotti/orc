"""Service protocols for the orc orchestrator.

Defines structural Protocol types for the domain services consumed by
:class:`~orc.dispatcher.Dispatcher`.  These protocols replace the flat
:class:`~orc.dispatcher.DispatchCallbacks` bag as the *canonical* service
contract while remaining fully backwards-compatible (``DispatchCallbacks``
satisfies all three protocols).

Having named protocols rather than a single callbacks dataclass improves:

* **Testability** — test doubles only need to implement the methods they care about.
* **Discoverability** — each service has a clear, documented responsibility.
* **Extensibility** — new backends / boards / messaging providers can be plugged in
  without changing the dispatcher.

Usage example::

    class MyBoardService:
        def get_open_tasks(self) -> list[dict]:
            return []
        # ... other methods ...

    # Type-check that MyBoardService satisfies the protocol:
    assert isinstance(MyBoardService(), BoardService)
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class BoardService(Protocol):
    """Read/write access to the kanban board and pending-work queries."""

    def get_open_tasks(self) -> list[dict]:
        """Return the list of open task dicts from board.yaml."""
        ...

    def assign_task(self, task_name: str, agent_id: str) -> None:
        """Write ``assigned_to: {agent_id}`` for *task_name* in board.yaml."""
        ...

    def unassign_task(self, task_name: str) -> None:
        """Clear the ``assigned_to`` field for *task_name* in board.yaml."""
        ...

    def get_pending_visions(self) -> list[str]:
        """Return vision .md filenames with no matching board task."""
        ...

    def get_pending_reviews(self) -> list[str]:
        """Return feat/* branches not yet merged into dev."""
        ...


@runtime_checkable
class WorktreeService(Protocol):
    """Git worktree lifecycle management."""

    def ensure_feature_worktree(self, task_name: str) -> Path:
        """Ensure the feature worktree for *task_name* exists; return its path."""
        ...

    def ensure_dev_worktree(self) -> Path:
        """Ensure the dev worktree exists; return its path."""
        ...


@runtime_checkable
class MessagingService(Protocol):
    """Telegram message send / receive and boot-message helpers."""

    def get_messages(self) -> list[dict]:
        """Fetch the latest Telegram message history."""
        ...

    def has_unresolved_block(self, messages: list[dict]) -> tuple[str | None, str | None]:
        """Return ``(agent_id, state)`` if there is an unresolved block."""
        ...

    def wait_for_human_reply(self, messages: list[dict]) -> str:
        """Block until a human replies on Telegram; return the reply text."""
        ...

    def post_boot_message(self, agent_id: str, body: str) -> None:
        """Send ``[{agent_id}](boot) …`` to Telegram."""
        ...

    def post_resolved(self, blocked_agent: str, blocked_state: str, resolver: str) -> None:
        """Send ``[orc](resolved) …`` to Telegram."""
        ...

    def boot_message_body(self) -> str:
        """Return the body text for a boot message."""
        ...
