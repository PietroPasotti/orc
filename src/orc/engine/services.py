"""Service protocols for the orc orchestrator.

Defines structural Protocol types for the domain services consumed by
:class:`~orc.engine.dispatcher.Dispatcher`.

Having named protocols rather than a single callbacks dataclass improves:

* **Testability** — test doubles only need to implement the methods they care about.
* **Discoverability** — each service has a clear, documented responsibility.
* **Extensibility** — new backends / boards / messaging providers can be plugged in
  without changing the dispatcher.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from orc.ai.backends import SpawnResult
from orc.coordination.models import TaskBody, TaskEntry
from orc.engine.context import TodoItem
from orc.messaging.messages import ChatMessage
from orc.squad import AgentRole


@runtime_checkable
class BoardService(Protocol):
    """Read/write access to the kanban board and pending-work queries."""

    def get_tasks(self) -> list[TaskEntry]:
        """Return the list of open task entries from board.yaml."""
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

    def get_blocked_tasks(self) -> list[str]:
        """Return task names with blocked status."""
        ...

    def scan_todos(self) -> list[TodoItem]:
        """Return TO-DO/FIX-ME comment items from the repository source."""
        ...

    def is_empty(self) -> bool:
        """Return True when there is no pending work on the board."""
        ...

    def query_tasks(self, status: str) -> list[str]:
        """Return task names whose board status equals *status*."""
        ...

    def delete_task(self, task_name: str) -> None:
        """Remove *task_name* from the board and delete its task file."""
        ...

    def set_task_status(self, task_name: str, status: str) -> None:
        """Set the ``status`` field of *task_name* in board.yaml."""
        ...

    def read_vision(self, name: str) -> str:
        """Return the content of a vision file from ``vision/ready/``."""
        ...

    def close_vision(self, name: str, summary: str, task_files: list[str]) -> None:
        """Move a vision from ``vision/ready/`` to ``vision/done/``."""
        ...

    def read_task_content(self, task_name: str) -> str:
        """Return the raw markdown content of a task file."""
        ...

    def create_task(self, title: str, vision: str, body: TaskBody) -> tuple[str, Path]:
        """Create a task file and add a planned entry to board.yaml."""
        ...

    def add_task_comment(self, task_name: str, author: str, text: str) -> None:
        """Append a comment to a task entry."""
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

    def cleanup_feature_worktree(self, task_name: str) -> None:
        """Remove the feature worktree and branch for *task_name* (idempotent)."""
        ...


@runtime_checkable
class MessagingService(Protocol):
    """Telegram messaging (write-only for agents — send status updates to user)."""

    def get_messages(self) -> list[ChatMessage]:
        """Fetch the latest Telegram message history."""
        ...

    def post_boot_message(self, agent_id: str, body: str) -> None:
        """Format and send a ``[{agent_id}](boot) …`` message to Telegram."""
        ...


@runtime_checkable
class WorkflowService(Protocol):
    """Workflow-level operations: task-state routing, merging, and crash-recovery."""

    def derive_task_state(
        self, task_name: str, task_data: TaskEntry | None = None
    ) -> tuple[str, str]:
        """Return ``(token, reason)`` for *task_name*.

        *task_data* is the task's board entry (avoids a redundant board
        read when the caller already has it).  *token* is a role name or one
        of the sentinels ``QA_PASSED`` / ``CLOSE_BOARD`` defined in
        :mod:`orc.engine.dispatcher`.
        """
        ...

    def merge_feature(self, task_name: str) -> None:
        """Merge the feature branch for *task_name* into dev."""
        ...


@runtime_checkable
class AgentService(Protocol):
    """Context building and agent subprocess spawning."""

    def build_context(
        self,
        role: AgentRole,
        agent_id: str,
        task_name: str | None = None,
    ) -> tuple[str, tuple[str, str]]:
        """Return ``(model, (system_prompt, user_prompt))`` for an agent."""
        ...

    def spawn(
        self,
        context: tuple[str, str],
        cwd: Path,
        model: str | None,
        log_path: Path | None,
        agent_id: str | None = None,
        role: AgentRole | None = None,
    ) -> SpawnResult:
        """Spawn an agent subprocess; return a :class:`~orc.ai.backends.SpawnResult`."""
        ...
