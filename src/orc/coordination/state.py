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

import structlog

from orc.coordination.board._manager import FileBoardManager, TaskStatus
from orc.coordination.models import Board, TaskBody, TaskEntry

logger = structlog.get_logger(__name__)

_P_args = tuple[object, ...]
_P_kwargs = dict[str, object]


def _locked[R](method: Callable[..., R]) -> Callable[..., R]:
    """Acquire ``self._lock`` around *method* and return its result."""

    @wraps(method)
    def _wrapper(*args: object, **kwargs: object) -> R:
        self = args[0]
        with self._lock:  # type: ignore[attr-defined]
            return method(*args, **kwargs)

    return _wrapper


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
        self._lock = threading.RLock()

    # ── Board: queries ────────────────────────────────────────────────────

    @_locked
    def get_tasks(self) -> list[TaskEntry]:
        """Return all tasks from board.yaml."""
        return list(self._mgr.read_board().tasks)

    def query_tasks(self, status: str) -> list[str]:
        """Return names of tasks whose ``status`` field matches *status*."""
        return [t.name for t in self.get_tasks() if t.status == status]

    def get_blocked_tasks(self) -> list[str]:
        """Return names of tasks with ``status == "blocked"``."""
        return self.query_tasks("blocked")

    @_locked
    def delete_task(self, task_name: str) -> None:
        """Remove *task_name* from board.yaml and delete its task file."""
        board = self._mgr.read_board()
        board.tasks = [t for t in board.tasks if t.name != task_name]
        self._mgr.write_board(board)
        self._mgr.delete_task_file(task_name)

    @_locked
    def read_work_summary(self) -> str:
        """Return a human-readable kanban overview (board metadata only).

        Task file contents and comments are intentionally excluded so that
        agents stay focused.  Agents should use the ``share/get_task.py``
        tool to fetch a task's full details and conversation on demand.
        """
        board = self._mgr.read_board()
        if not board.tasks and self._mgr.board_path.exists() is False:
            return "_No active work._"

        # Render only name / status / assigned_to — no comments, no task files.
        task_lines: list[str] = []
        for t in board.tasks:
            task_lines.append(f"  - name: {t.name}")
            if t.status is not None:
                task_lines.append(f"    status: {t.status}")
            if t.assigned_to is not None:
                task_lines.append(f"    assigned_to: {t.assigned_to}")

        tasks_block = "\n".join(task_lines) if task_lines else "  []"
        body = f"counter: {board.counter}\ntasks:\n{tasks_block}"
        return f"### board.yaml\n\n```yaml\n{body}\n```"

    def read_task_content(self, task_name: str) -> str:
        """Return the raw markdown content of *task_name*'s task file.

        Raises :class:`FileNotFoundError` if *task_name* is not found.
        """
        task_path = self._mgr.work_dir / task_name
        if not task_path.exists():
            raise FileNotFoundError(f"Task file not found: {task_name}")
        return task_path.read_text()

    @_locked
    def get_task(self, task_name: str) -> TaskEntry | None:
        """Return the board entry for *task_name*, or ``None`` if absent."""
        return self._mgr.get_task(task_name)

    @_locked
    def create_task(self, title: str, vision: str, body: TaskBody) -> tuple[str, Path]:
        """Create a task file and add a *planned* entry to board.yaml.

        *vision* is the filename of the vision this task was refined from.

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
        for entry in board.tasks:
            if entry.name == task_name:
                entry.assigned_to = agent_id
                if entry.status in (None, "", TaskStatus.PLANNED):
                    entry.status = TaskStatus.IN_PROGRESS
                self._mgr.write_board(board)
                return
        logger.warning("assign_task: task not found", task=task_name)

    @_locked
    def unassign_task(self, task_name: str) -> None:
        """Clear the ``assigned_to`` field for *task_name* in board.yaml."""
        board = self._mgr.read_board()
        changed = False
        for entry in board.tasks:
            if entry.name == task_name:
                entry.assigned_to = None
                changed = True
                break
        if changed:
            self._mgr.write_board(board)

    @_locked
    def clear_all_assignments(self) -> None:
        """Clear all ``assigned_to`` fields — called on startup for crash recovery."""
        board = self._mgr.read_board()
        changed = False
        for entry in board.tasks:
            if entry.assigned_to is not None:
                entry.assigned_to = None
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
        """Return vision ``.md`` filenames from ``vision/ready/`` with no matching board task.

        A vision is considered "handled" if any board task file references it
        in its ``**Vision:** <filename>`` header line.
        """
        ready_dir = self._mgr.vision_dir / "ready"
        if not ready_dir.is_dir():
            return []
        # Collect vision filenames referenced by existing task files.
        referenced_visions: set[str] = set()
        for task_path in self._mgr.list_task_files():
            try:
                for line in task_path.read_text().splitlines()[:15]:
                    if line.startswith("**Vision:**"):
                        ref = line.split("**Vision:**", 1)[1].strip()
                        referenced_visions.add(ref)
                        break
            except OSError:
                continue
        result = []
        for f in sorted(ready_dir.glob("*.md")):
            if f.name.lower().startswith(".") or f.name.lower() == "readme.md":
                continue
            if f.name not in referenced_visions:
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
        are merged into dev (see :func:`orc.engine.workflow._merge_feature_into_dev`).
        """
        with self._lock:
            vision_path = self._mgr.vision_dir / "ready" / name
            if not vision_path.exists():
                raise FileNotFoundError(f"Vision not found: {name}")

        done_dir = self._mgr.vision_dir / "done"
        done_dir.mkdir(exist_ok=True)
        vision_path.rename(done_dir / name)

        logger.info("closed vision", name=name)

    def _read_board(self) -> Board:
        """Return the raw board (used internally by tests and legacy callers)."""
        return self._mgr.read_board()
