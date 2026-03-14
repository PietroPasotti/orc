"""orc – board YAML CRUD operations.

All public functions delegate to the module-level :data:`_manager` singleton
(:class:`~orc.board_manager.FileBoardManager`).  Call :func:`init_manager`
once (done automatically by :func:`orc.config.init`).
"""

from __future__ import annotations

import structlog

import orc.config as _cfg

logger = structlog.get_logger(__name__)

_manager = None


def init_manager() -> None:
    """Initialise (or reinitialise) the module-level BoardManager from Config."""
    global _manager
    from orc.board_manager import FileBoardManager  # noqa: PLC0415

    _manager = FileBoardManager(_cfg.get().cache_dir)


def _get_manager():
    global _manager
    from orc.board_manager import FileBoardManager  # noqa: PLC0415

    work_dir = _cfg.get().work_dir
    if _manager is None or _manager._work_dir != work_dir:
        _manager = FileBoardManager(work_dir.parent)
    return _manager


def _read_board() -> dict:
    return _get_manager().read_board()


def _write_board(board: dict) -> None:
    _get_manager().write_board(board)


def get_open_tasks() -> list[dict]:
    """Return the list of open task dicts from board.yaml."""
    board = _read_board()
    result = []
    for t in board.get("open", []):
        if isinstance(t, dict):
            result.append(t)
        else:
            result.append({"name": str(t)})
    return result


def get_task(task_name: str) -> dict | None:
    """Return the board entry for *task_name*, or ``None`` if absent."""
    return _get_manager().get_task(task_name)


def set_task_status(task_name: str, status: str) -> None:
    """Set the ``status`` field of *task_name* in board.yaml."""
    _get_manager().set_task_status(task_name, status)


def add_task_comment(task_name: str, author: str, text: str) -> None:
    """Append a comment to *task_name*'s ``comments`` list."""
    _get_manager().add_task_comment(task_name, author, text)


def has_open_work() -> bool:
    """Return True if there is at least one open task on the board."""
    return bool(get_open_tasks())


def assign_task(task_name: str, agent_id: str) -> None:
    """Write ``assigned_to: {agent_id}`` for *task_name* and set status to ``coding``."""
    board = _read_board()
    for t in board.get("open", []):
        if isinstance(t, dict) and t.get("name") == task_name:
            t["assigned_to"] = agent_id
            if t.get("status") in (None, "", "planned", "rejected"):
                t["status"] = "coding"
            _write_board(board)
            logger.debug("task assigned", task=task_name, agent_id=agent_id)
            return
    logger.warning("assign_task: task not found in board", task=task_name)


def unassign_task(task_name: str) -> None:
    """Clear the ``assigned_to`` field for *task_name* in board.yaml."""
    board = _read_board()
    changed = False
    for t in board.get("open", []):
        if isinstance(t, dict) and t.get("name") == task_name:
            t.pop("assigned_to", None)
            changed = True
            break
    if changed:
        _write_board(board)
        logger.debug("task unassigned", task=task_name)


def clear_all_assignments() -> None:
    """Clear all ``assigned_to`` fields — called on startup for crash recovery."""
    board = _read_board()
    changed = False
    for t in board.get("open", []):
        if isinstance(t, dict) and t.pop("assigned_to", None) is not None:
            changed = True
    if changed:
        _write_board(board)
        logger.info("cleared stale task assignments on startup")


def _active_task_name() -> str | None:
    """Return the file name of the first open task, or None if the board is empty."""
    board = _read_board()
    open_tasks = board.get("open", [])
    if not open_tasks:
        return None
    first = open_tasks[0]
    return first["name"] if isinstance(first, dict) else str(first)


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
