"""orc.coordination.board – board storage and CRUD helpers.

Public API
----------
- :class:`TaskStatus` – kanban swimlane status enum
- :class:`FileBoardManager` – filesystem-backed board manager
- :func:`init_manager` / :func:`get_tasks` / :func:`get_task` /
  :func:`set_task_status` / :func:`add_task_comment` /
  :func:`assign_task` / :func:`unassign_task` /
  :func:`clear_all_assignments` – module-level board helpers (CLI / engine)
"""

from orc.coordination.board._board import (
    _read_board,  # noqa: F401
    _read_work,  # noqa: F401
    add_task_comment,
    assign_task,
    clear_all_assignments,
    delete_task,
    get_task,
    get_tasks,
    init_manager,
    set_task_status,
    unassign_task,
)
from orc.coordination.board._manager import FileBoardManager, TaskStatus

__all__ = [
    "TaskStatus",
    "FileBoardManager",
    "init_manager",
    "get_tasks",
    "get_task",
    "set_task_status",
    "add_task_comment",
    "assign_task",
    "unassign_task",
    "clear_all_assignments",
    "delete_task",
]
