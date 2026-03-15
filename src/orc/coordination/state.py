"""Thread-safe state manager for the orc coordination API.

:class:`BoardStateManager` wraps :class:`~orc.coordination.board.FileBoardManager`
with a :class:`threading.RLock` so that concurrent HTTP request handlers
(running in anyio's thread pool) and the orchestrator's main thread can
safely share board and vision state.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from functools import wraps
from pathlib import Path

import pydantic
import structlog

from orc.coordination.board import FileBoardManager, TaskStatus

logger = structlog.get_logger(__name__)


def _locked[**P, R](method: Callable[P, R]) -> Callable[P, R]:
    """Acquire ``self._lock`` around *method* and return its result."""

    @wraps(method)
    def _wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        self = args[0]
        with self._lock:  # type: ignore[attr-defined]
            return method(*args, **kwargs)

    return _wrapper  # type: ignore[return-value]


class CommentAuthor(pydantic.BaseModel):
    # could be agent-1 but also 'pietro' or 'jeff'
    name: str


class Comment(pydantic.BaseModel):
    body: str
    author: CommentAuthor


class Task(pydantic.BaseModel):
    name: str
    status: str
    branch: str | None = None
    worktree: Path | None = None
    assigned_to: str | None = None
    comments: tuple[Comment] = ()


class BoardStateManager:
    """Thread-safe coordinator for board and vision state.

    Parameters
    ----------
    orc_dir:
        Absolute path to the orc configuration directory (e.g.
        ``{project}/.orc``).  All board and vision I/O is rooted here.
    """

    def __init__(self, orc_dir: Path) -> None:
        self._mgr = FileBoardManager(orc_dir)
        self._orc_dir = orc_dir
        self._lock = threading.RLock()

    # ── Board: queries ────────────────────────────────────────────────────

    @_locked
    def get_tasks(self) -> list[Task]:
        """Return all tasks from board.yaml."""
        board = self._mgr.read_board()
        result = []
        for t in board.get("tasks", []):
            result.append(t if isinstance(t, dict) else {"name": str(t)})
        return result

    def query_tasks(self, status: str) -> list[str]:
        """Return names of tasks whose ``status`` field matches *status*."""
        return [
            t["name"] for t in self.get_tasks() if isinstance(t, dict) and t.get("status") == status
        ]

    def get_blocked_tasks(self) -> list[str]:
        """Return names of tasks with ``status == "blocked"``."""
        return self.query_tasks("blocked")

    def read_task_content(self, task_name: str) -> str:
        """Return the raw markdown content of *task_name*'s task file.

        Raises :class:`FileNotFoundError` if *task_name* is not found.
        """
        task_path = self._mgr.work_dir / task_name
        if not task_path.exists():
            raise FileNotFoundError(f"Task file not found: {task_name}")
        return task_path.read_text()

    @_locked
    def get_task(self, task_name: str) -> dict | None:
        """Return the board entry for *task_name*, or ``None`` if absent."""
        return self._mgr.get_task(task_name)

    @_locked
    def create_task(self, title: str, vision: str, body: dict) -> tuple[str, Path]:
        """Create a task file and add a *planned* entry to board.yaml.

        *vision* is the filename of the vision this task was refined from.
        *body* is a dict with keys: overview, in_scope, out_of_scope, steps, notes.

        Returns ``(filename, absolute_path)`` of the created task file.
        """
        return self._mgr.create_task(title, vision, body)

    @_locked
    def set_task_status(self, task_name: str, status: str) -> None:
        """Set the ``status`` field of *task_name* in board.yaml."""
        self._mgr.set_task_status(task_name, status)

    @_locked
    def assign_task(self, task_name: str, agent_id: str) -> None:
        """Write ``assigned_to: {agent_id}`` for *task_name* in board.yaml."""
        board = self._mgr.read_board()
        for t in board.get("tasks", []):
            if isinstance(t, dict) and t.get("name") == task_name:
                t["assigned_to"] = agent_id
                if t.get("status") in (None, "", TaskStatus.PLANNED):
                    t["status"] = TaskStatus.IN_PROGRESS
                self._mgr.write_board(board)
                return
        logger.warning("assign_task: task not found", task=task_name)

    @_locked
    def unassign_task(self, task_name: str) -> None:
        """Clear the ``assigned_to`` field for *task_name* in board.yaml."""
        board = self._mgr.read_board()
        changed = False
        for t in board.get("tasks", []):
            if isinstance(t, dict) and t.get("name") == task_name:
                t.pop("assigned_to", None)
                changed = True
                break
        if changed:
            self._mgr.write_board(board)

    @_locked
    def clear_all_assignments(self) -> None:
        """Clear all ``assigned_to`` fields — called on startup for crash recovery."""
        board = self._mgr.read_board()
        changed = False
        for t in board.get("tasks", []):
            if isinstance(t, dict) and t.pop("assigned_to", None) is not None:
                changed = True
        if changed:
            self._mgr.write_board(board)
            logger.info("cleared stale task assignments on startup")

    @_locked
    def add_task_comment(self, task_name: str, author: str, text: str) -> None:
        """Append a comment to the *task_name* entry's ``comments`` list."""
        self._mgr.add_task_comment(task_name, author, text)

    # ── Visions ───────────────────────────────────────────────────────────

    @_locked
    def get_pending_visions(self) -> list[str]:
        """Return vision ``.md`` filenames from ``vision/ready/`` with no matching board task."""
        ready_dir = self._mgr.vision_dir / "ready"
        if not ready_dir.is_dir():
            return []
        board = self._mgr.read_board()
        all_task_stems = {
            (t["name"] if isinstance(t, dict) else str(t)) for t in board.get("tasks", [])
        }
        result = []
        for f in sorted(ready_dir.glob("*.md")):
            if f.name.lower().startswith(".") or f.name.lower() == "readme.md":
                continue
            if not any(stem == f.name or stem.startswith(f.stem) for stem in all_task_stems):
                result.append(f.name)
        return result

    def read_vision(self, name: str) -> str:
        """Return the content of a vision file from ``vision/ready/``.

        Raises :class:`FileNotFoundError` if *name* is not found.
        """
        vision_path = self._mgr.vision_dir / "ready" / name
        if not vision_path.exists():
            raise FileNotFoundError(f"Vision not found: {name}")
        return vision_path.read_text()

    def close_vision(self, name: str, summary: str, task_files: list[str]) -> None:
        """Move a vision from ``vision/ready/`` to ``vision/done/``.

        Raises :class:`FileNotFoundError` if *name* is not found in ``ready/``.

        Note: ``summary`` and ``task_files`` are accepted for API compatibility but
        are no longer used here.  Changelog entries are written when task branches
        are merged into dev (see :func:`orc.git.core._merge_feature_into_dev`).
        """
        with self._lock:
            vision_path = self._mgr.vision_dir / "ready" / name
            if not vision_path.exists():
                raise FileNotFoundError(f"Vision not found: {name}")

        done_dir = self._mgr.vision_dir / "done"
        done_dir.mkdir(exist_ok=True)
        vision_path.rename(done_dir / name)

        logger.info("closed vision", name=name)
