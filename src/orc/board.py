"""orc – board YAML CRUD operations."""

from __future__ import annotations

from pathlib import Path

import structlog
import yaml

import orc.config as _cfg

logger = structlog.get_logger(__name__)


def _dev_board_file() -> Path:
    """Return the board.yaml that is currently authoritative."""
    try:
        cfg = _cfg.get()
        rel = cfg.orc_dir.relative_to(cfg.repo_root)
    except ValueError:
        rel = Path(_cfg.get().orc_dir.name)
    cfg = _cfg.get()
    candidate = cfg.dev_worktree / rel / "work" / "board.yaml"
    return candidate if candidate.exists() else cfg.board_file


def _read_board() -> dict:
    """Parse board.yaml and return its full structure (empty dict on error)."""
    path = _dev_board_file()
    if not path.exists():
        return {"counter": 0, "open": [], "done": []}
    try:
        data = yaml.safe_load(path.read_text()) or {}
        data.setdefault("open", [])
        data.setdefault("done", [])
        return data
    except Exception:
        logger.debug("_read_board: failed to parse board file", path=str(path), exc_info=True)
        return {"counter": 0, "open": [], "done": []}


def _write_board(board: dict) -> None:
    """Persist *board* to the authoritative board.yaml path.

    Uses an atomic write (temp file + ``rename``) so a crash during the write
    never leaves board.yaml in a partially-written state.
    """
    path = _dev_board_file()
    content = yaml.dump(board, default_flow_style=False, allow_unicode=True)
    tmp = path.with_suffix(".yaml.tmp")
    try:
        tmp.write_text(content)
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


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


def assign_task(task_name: str, agent_id: str) -> None:
    """Write ``assigned_to: {agent_id}`` for *task_name* in board.yaml."""
    board = _read_board()
    for t in board.get("open", []):
        if isinstance(t, dict) and t.get("name") == task_name:
            t["assigned_to"] = agent_id
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


def has_open_work() -> bool:
    """Return ``True`` if board.yaml has at least one task in the open list."""
    return _active_task_name() is not None


def _read_work(*, active_only: str | None = None) -> str:
    """Return a human-readable summary of the kanban board + open task files.

    When *active_only* is provided (a task filename such as ``0001-task.md``),
    only that task's full content is included; other open tasks are listed by
    name only.  This dramatically reduces token cost for coder/QA agents that
    work on a single task.
    """
    parts: list[str] = []

    board_path = _dev_board_file()
    if board_path.exists():
        parts.append(f"### orc/work/board.yaml\n\n```yaml\n{board_path.read_text().strip()}\n```")

    work_dir = board_path.parent if board_path.exists() else _cfg.get().work_dir
    for task_file in sorted(work_dir.glob("*.md")):
        if task_file.name.lower() == "readme.md":
            continue
        if active_only and task_file.name != active_only:
            parts.append(f"### {task_file.name} _(summary only)_")
        else:
            parts.append(f"### {task_file.name}\n\n{task_file.read_text()}")

    return "\n\n".join(parts) if parts else "_No active work._"


if __name__ == "__main__":  # pragma: no cover
    from orc.config import init

    init(Path("/home/pietro/hacking/orc/.orc"))
    print(_dev_board_file().read_text())  # noqa: T201
