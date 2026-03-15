"""orc – board YAML CRUD operations.

All public functions delegate to the module-level :data:`_manager` singleton
(:class:`~orc.coordination.board._manager.FileBoardManager`).  Call
:func:`init_manager` once (done automatically by :func:`orc.config.init`).

.. note::

    The ``orc run`` dispatch loop does **not** use this module directly.
    It uses :class:`~orc.coordination.BoardStateManager`, which is the single
    thread-safe source of truth during a run.  This module is used by CLI
    read commands (``orc status``) and internal engine helpers.
"""

from __future__ import annotations

from pathlib import Path

import structlog

import orc.config as _cfg
from orc.coordination.board._manager import FileBoardManager, TaskStatus
from orc.coordination.models import Board, TaskBody, TaskEntry

logger = structlog.get_logger(__name__)

_manager: FileBoardManager | None = None


def init_manager() -> None:
    """Initialise (or reinitialise) the module-level BoardManager from Config."""
    global _manager
    _manager = FileBoardManager(_cfg.get().orc_dir)


def _get_manager() -> FileBoardManager:
    global _manager
    orc_dir = _cfg.get().orc_dir
    if _manager is None or _manager._work_dir != orc_dir / "work":
        _manager = FileBoardManager(orc_dir)
    return _manager


def _read_board() -> Board:
    return _get_manager().read_board()


def _write_board(board: Board) -> None:
    _get_manager().write_board(board)


def get_tasks() -> list[TaskEntry]:
    """Return the list of task entries from board.yaml."""
    return _read_board().tasks


def get_task(task_name: str) -> TaskEntry | None:
    """Return the board entry for *task_name*, or ``None`` if absent."""
    return _get_manager().get_task(task_name)


def set_task_status(task_name: str, status: str) -> None:
    """Set the ``status`` field of *task_name* in board.yaml."""
    _get_manager().set_task_status(task_name, status)


def add_task_comment(task_name: str, author: str, text: str) -> None:
    """Append a comment to *task_name*'s ``comments`` list."""
    _get_manager().add_task_comment(task_name, author, text)


def assign_task(task_name: str, agent_id: str) -> None:
    """Write ``assigned_to: {agent_id}`` for *task_name* and set status to ``in-progress``."""
    board = _read_board()
    for entry in board.tasks:
        if entry.name == task_name:
            entry.assigned_to = agent_id
            if entry.status in (None, "", TaskStatus.PLANNED):
                entry.status = TaskStatus.IN_PROGRESS
            _write_board(board)
            logger.debug("task assigned", task=task_name, agent_id=agent_id)
            return
    logger.warning("assign_task: task not found in board", task=task_name)


def unassign_task(task_name: str) -> None:
    """Clear the ``assigned_to`` field for *task_name* in board.yaml."""
    board = _read_board()
    changed = False
    for entry in board.tasks:
        if entry.name == task_name:
            entry.assigned_to = None
            changed = True
            break
    if changed:
        _write_board(board)
        logger.debug("task unassigned", task=task_name)


def clear_all_assignments() -> None:
    """Clear all ``assigned_to`` fields — called on startup for crash recovery."""
    board = _read_board()
    changed = False
    for entry in board.tasks:
        if entry.assigned_to is not None:
            entry.assigned_to = None
            changed = True
    if changed:
        _write_board(board)
        logger.info("cleared stale task assignments on startup")


def delete_task(task_name: str) -> None:
    """Remove *task_name* from board.yaml and delete its task file."""
    mgr = _get_manager()
    board = _read_board()
    board.tasks = [t for t in board.tasks if t.name != task_name]
    _write_board(board)
    mgr.delete_task_file(task_name)


def _active_task_name() -> str | None:
    """Return the file name of the first task, or None if the board is empty."""
    tasks = _read_board().tasks
    if not tasks:
        return None
    return tasks[0].name


def _read_work(*, active_only: str | None = None) -> str:
    """Return a human-readable summary of the kanban board + open task files.

    When *active_only* is provided only that task's full content is included;
    others are listed by name only to reduce token cost.
    """
    mgr = _get_manager()
    parts: list[str] = []

    board_path = mgr.board_path
    if board_path.exists():
        parts.append(f"### board.yaml\n\n```yaml\n{board_path.read_text().strip()}\n```")

    for task_file in mgr.list_task_files():
        if active_only and task_file.name != active_only:
            parts.append(f"### {task_file.name} _(summary only)_")
        else:
            parts.append(f"### {task_file.name}\n\n{task_file.read_text()}")

    return "\n\n".join(parts) if parts else "_No active work._"


def create_task(title: str, vision: str, body: TaskBody) -> tuple[str, Path]:
    """Create a task file and add a *planned* entry to board.yaml."""
    return _get_manager().create_task(title, vision, body)
