"""orc – BoardManager ABC and FileBoardManager implementation.

:class:`BoardManager` provides an abstract interface for all board, task,
and vision storage operations.  :class:`FileBoardManager` implements it
using the filesystem (``cache_dir/work/`` and ``cache_dir/vision/``).

The abstraction lets the storage backend be replaced in the future (e.g.
SQLite, a remote API) by swapping only this module.

This is the only module that should read/write board.yaml and task/vision .md files.
"""

from __future__ import annotations

import abc
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import structlog
import yaml
from filelock import FileLock

from orc.coordination.models import Board, TaskBody, TaskComment, TaskEntry

logger = structlog.get_logger(__name__)

_LOCK_TIMEOUT = 30  # seconds to wait before raising FileLockError

# ── Task status enum (kanban swimlane model) ──────────────────────────────


class TaskStatus(StrEnum):
    PLANNED = "planned"  # Not being worked on yet, but ready to start
    BLOCKED = "blocked"  # Needs (human) intervention
    IN_PROGRESS = "in-progress"  # Agent working on this
    IN_REVIEW = "in-review"  # Awaiting QA
    DONE = "done"  # QA approved, ready to merge


TASK_STATUSES: frozenset[str] = frozenset(TaskStatus)


# Represent TaskStatus as a plain YAML string so round-trips work with both
# yaml.dump (default Dumper) and yaml.safe_dump (SafeDumper).
def _represent_task_status(dumper: yaml.Dumper, val: TaskStatus) -> yaml.ScalarNode:
    return dumper.represent_str(str(val))


yaml.add_representer(TaskStatus, _represent_task_status)
yaml.add_representer(TaskStatus, _represent_task_status, Dumper=yaml.SafeDumper)  # type: ignore[arg-type]


# ── YAML serialisation helpers ────────────────────────────────────────────


def _board_to_dict(board: Board) -> dict[str, object]:
    """Convert a :class:`Board` to a plain dict suitable for ``yaml.safe_dump``."""
    tasks: list[dict[str, object]] = []
    for entry in board.tasks:
        task_dict: dict[str, object] = {"name": entry.name}
        if entry.status is not None:
            task_dict["status"] = entry.status
        if entry.assigned_to is not None:
            task_dict["assigned_to"] = entry.assigned_to
        if entry.comments:
            task_dict["comments"] = [
                {"from": c.from_, "text": c.text, "ts": c.ts} for c in entry.comments
            ]
        if entry.commit_tag is not None:
            task_dict["commit_tag"] = entry.commit_tag
        if entry.timestamp is not None:
            task_dict["timestamp"] = entry.timestamp
        tasks.append(task_dict)
    return {"counter": board.counter, "tasks": tasks}


def _board_from_dict(raw: dict[str, object]) -> Board:
    """Parse raw YAML dict to a :class:`Board`, coercing legacy string entries."""
    counter = int(raw.get("counter") or 0)  # type: ignore[call-overload]
    raw_tasks = raw.get("tasks") or []
    if not isinstance(raw_tasks, list):
        raw_tasks = []
    tasks: list[TaskEntry] = []
    for t in raw_tasks:
        if isinstance(t, dict):
            tasks.append(TaskEntry.model_validate(t))
        else:
            tasks.append(TaskEntry(name=str(t)))
    return Board(counter=counter, tasks=tasks)


# ── Abstract base class ───────────────────────────────────────────────────


class BoardManager(abc.ABC):
    """Abstract interface for board, task-file, and vision-file storage."""

    # ── Board YAML CRUD ──────────────────────────────────────────────────

    @abc.abstractmethod
    def read_board(self) -> Board:
        """Parse board.yaml and return its full structure.

        Returns a default :class:`Board` on any read/parse error rather than raising.
        """

    @abc.abstractmethod
    def write_board(self, board: Board) -> None:
        """Persist *board* atomically (temp file + rename)."""

    # ── Task status + comments (read-modify-write helpers) ───────────────

    @abc.abstractmethod
    def get_task(self, task_name: str) -> TaskEntry | None:
        """Return the board entry for *task_name*, or ``None`` if absent."""

    @abc.abstractmethod
    def set_task_status(self, task_name: str, status: str) -> None:
        """Set the ``status`` field of *task_name* in board.yaml.

        Does nothing (with a warning) if the task is not on the tasks list.
        """

    @abc.abstractmethod
    def add_task_comment(self, task_name: str, author: str, text: str) -> None:
        """Append a comment to the *task_name* entry's ``comments`` list.

        The comment has the shape::

            TaskComment(from_=author, text=text, ts="<ISO-8601>")
        """

    # ── Task file CRUD ───────────────────────────────────────────────────

    @abc.abstractmethod
    def create_task(self, title: str, vision: str, body: TaskBody) -> tuple[str, Path]:
        """Create a task .md file and add a *planned* entry to the board.

        *vision* is the filename of the vision this task was refined from.
        *body* contains the structured task content.

        Returns ``(filename, absolute_path)`` of the created file.
        The counter in board.yaml is atomically incremented.
        """

    @abc.abstractmethod
    def list_task_files(self) -> list[Path]:
        """Return sorted ``*.md`` paths in work_dir (excluding README.md)."""

    @abc.abstractmethod
    def delete_task_file(self, name: str) -> None:
        """Delete the task file *name*.  No-op if it does not exist."""

    # ── Path accessors ───────────────────────────────────────────────────

    @property
    @abc.abstractmethod
    def board_path(self) -> Path:
        """Absolute path to board.yaml."""

    @property
    @abc.abstractmethod
    def work_dir(self) -> Path:
        """Directory that contains board.yaml and task ``.md`` files."""

    @property
    @abc.abstractmethod
    def vision_dir(self) -> Path:
        """Directory that contains vision ``.md`` files."""


# ── Filesystem implementation ─────────────────────────────────────────────


class FileBoardManager(BoardManager):
    """BoardManager backed by the local filesystem.

    *orc_dir* is the orc configuration directory (e.g. ``{project}/.orc``).
    Work files live under ``orc_dir/work/`` and vision files under
    ``orc_dir/vision/``.  Both directories are excluded from git via
    ``.orc/.gitignore``.
    """

    def __init__(self, orc_dir: Path) -> None:
        self._work_dir = orc_dir / "work"
        self._vision_dir = orc_dir / "vision"

    # ── Path accessors ───────────────────────────────────────────────────

    @property
    def board_path(self) -> Path:
        return self._work_dir / "board.yaml"

    @property
    def _lock_path(self) -> Path:
        return self._work_dir / ".board.lock"

    @property
    def work_dir(self) -> Path:
        return self._work_dir

    @property
    def vision_dir(self) -> Path:
        return self._vision_dir

    @contextmanager
    def _board_lock(self) -> Generator[None]:
        """Acquire an exclusive file lock for the duration of a board operation."""
        self._work_dir.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self._lock_path), timeout=_LOCK_TIMEOUT):
            yield

    # ── Board YAML CRUD ──────────────────────────────────────────────────

    def _read_board_raw(self) -> dict[str, object]:
        """Read board.yaml without acquiring the lock (caller must hold it)."""
        path = self.board_path
        if not path.exists():
            return {"counter": 0, "tasks": []}
        try:
            data: dict[str, object] = yaml.safe_load(path.read_text()) or {}
            data.setdefault("tasks", [])
            return data
        except Exception:
            logger.debug("read_board: failed to parse board file", path=str(path), exc_info=True)
            return {"counter": 0, "tasks": []}

    def _write_board_raw(self, board: Board) -> None:
        """Write board.yaml atomically without acquiring the lock (caller must hold it)."""
        path = self.board_path
        path.parent.mkdir(parents=True, exist_ok=True)
        content = yaml.safe_dump(
            _board_to_dict(board), default_flow_style=False, allow_unicode=True
        )
        tmp = path.with_suffix(".yaml.tmp")
        try:
            tmp.write_text(content)
            tmp.replace(path)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise

    def read_board(self) -> Board:
        with self._board_lock():
            return _board_from_dict(self._read_board_raw())

    def write_board(self, board: Board) -> None:
        with self._board_lock():
            self._write_board_raw(board)

    # ── Task status + comments ───────────────────────────────────────────

    def get_task(self, task_name: str) -> TaskEntry | None:
        with self._board_lock():
            raw = self._read_board_raw()
        board = _board_from_dict(raw)
        for entry in board.tasks:
            if entry.name == task_name:
                return entry
        return None

    def set_task_status(self, task_name: str, status: str) -> None:
        if status not in TASK_STATUSES:
            logger.warning("set_task_status: unknown status", status=status, task=task_name)
        with self._board_lock():
            raw = self._read_board_raw()
            board = _board_from_dict(raw)
            changed = False
            for entry in board.tasks:
                if entry.name == task_name:
                    entry.status = status
                    changed = True
                    break
            if changed:
                self._write_board_raw(board)
                logger.debug("task status updated", task=task_name, status=status)
            else:
                logger.warning("set_task_status: task not found", task=task_name)

    def add_task_comment(self, task_name: str, author: str, text: str) -> None:
        with self._board_lock():
            raw = self._read_board_raw()
            board = _board_from_dict(raw)
            changed = False
            for entry in board.tasks:
                if entry.name == task_name:
                    entry.comments.append(
                        TaskComment(
                            **{
                                "from": author,
                                "text": text,
                                "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            }
                        )
                    )
                    changed = True
                    break
            if changed:
                self._write_board_raw(board)
                logger.debug("task comment added", task=task_name, author=author)
            else:
                logger.warning("add_task_comment: task not found", task=task_name)

    # ── Task file CRUD ───────────────────────────────────────────────────

    @staticmethod
    def _render_task(task_id: str, title: str, vision: str, body: TaskBody) -> str:
        """Assemble a task markdown file from structured *body* content."""
        in_scope_lines = "\n".join(f"- {item}" for item in body.in_scope) if body.in_scope else "-"
        out_of_scope_lines = (
            "\n".join(f"- {item}" for item in body.out_of_scope) if body.out_of_scope else "-"
        )
        steps_lines = (
            "\n".join(f"- [ ] {i + 1}. {step}" for i, step in enumerate(body.steps))
            if body.steps
            else "- [ ]"
        )
        return (
            f"# {task_id}-{title}\n\n"
            f"**Vision:** {vision}\n\n"
            f"## Overview\n\n{body.overview}\n\n"
            f"## Scope\n\n"
            f"**In scope:**\n{in_scope_lines}\n\n"
            f"**Out of scope:**\n{out_of_scope_lines}\n\n"
            f"## Steps\n\n{steps_lines}\n\n"
            f"## Notes\n\n{body.notes}\n"
        )

    def create_task(self, title: str, vision: str, body: TaskBody) -> tuple[str, Path]:
        """Create a task .md file and add a *planned* entry to the board.

        The counter increment, file creation, and board write are performed
        atomically under the board lock.  Returns ``(filename, absolute_path)``.
        """
        with self._board_lock():
            raw = self._read_board_raw()
            board = _board_from_dict(raw)
            task_id = f"{board.counter:04d}"
            task_filename = f"{task_id}-{title}.md"
            task_file = self._work_dir / task_filename
            task_file.write_text(self._render_task(task_id, title, vision, body))
            board.tasks.append(TaskEntry(name=task_filename, status=TaskStatus.PLANNED))
            board.counter += 1
            self._write_board_raw(board)
        return task_filename, task_file

    def list_task_files(self) -> list[Path]:
        if not self._work_dir.is_dir():
            return []
        return sorted(p for p in self._work_dir.glob("*.md") if p.name.lower() != "readme.md")

    def delete_task_file(self, name: str) -> None:
        path = self._work_dir / name
        if path.exists():
            path.unlink()
            logger.info("deleted task file", path=str(path))
