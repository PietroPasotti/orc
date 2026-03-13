"""orc – BoardManager ABC and FileBoardManager implementation.

:class:`BoardManager` provides an abstract interface for all board, task,
and vision storage operations.  :class:`FileBoardManager` implements it
using the filesystem (``cache_dir/work/`` and ``cache_dir/vision/``).

The abstraction lets the storage backend be replaced in the future (e.g.
SQLite, a remote API) by swapping only this module.
"""

from __future__ import annotations

import abc
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
import yaml
from filelock import FileLock

logger = structlog.get_logger(__name__)

_LOCK_TIMEOUT = 30  # seconds to wait before raising FileLockError

# ── Valid task statuses ───────────────────────────────────────────────────

TASK_STATUSES = frozenset(
    {
        "planned",  # Planner created task, awaiting coder
        "coding",  # Coder actively working
        "review",  # Coder done, awaiting QA
        "approved",  # QA passed, ready to merge
        "rejected",  # QA failed, back to coder
        "blocked",  # Hard block, needs human help
        "soft-blocked",  # Soft block, planner can help
        "merged",  # Merged into dev (orchestrator sets; task moved to done shortly)
    }
)


# ── Abstract base class ───────────────────────────────────────────────────


class BoardManager(abc.ABC):
    """Abstract interface for board, task-file, and vision-file storage."""

    # ── Board YAML CRUD ──────────────────────────────────────────────────

    @abc.abstractmethod
    def read_board(self) -> dict:
        """Parse board.yaml and return its full structure.

        Returns a default ``{"counter": 0, "open": [], "done": []}`` on
        any read/parse error rather than raising.
        """

    @abc.abstractmethod
    def write_board(self, board: dict) -> None:
        """Persist *board* atomically (temp file + rename)."""

    # ── Task status + comments (read-modify-write helpers) ───────────────

    @abc.abstractmethod
    def get_task(self, task_name: str) -> dict | None:
        """Return the board entry dict for *task_name*, or ``None`` if absent."""

    @abc.abstractmethod
    def set_task_status(self, task_name: str, status: str) -> None:
        """Set the ``status`` field of *task_name* in board.yaml.

        Does nothing (with a warning) if the task is not on the open list.
        """

    @abc.abstractmethod
    def add_task_comment(self, task_name: str, author: str, text: str) -> None:
        """Append a comment to the *task_name* entry's ``comments`` list.

        The comment dict has the shape::

            {"from": author, "text": text, "ts": "<ISO-8601>"}
        """

    # ── Task file CRUD ───────────────────────────────────────────────────

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

    *cache_dir* is the per-project root (e.g.
    ``~/.cache/orc/projects/{uuid}``).  Work files live under
    ``cache_dir/work/`` and vision files under ``cache_dir/vision/``.
    """

    def __init__(self, cache_dir: Path) -> None:
        self._work_dir = cache_dir / "work"
        self._vision_dir = cache_dir / "vision"

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
    def _board_lock(self):
        """Acquire an exclusive file lock for the duration of a board operation."""
        self._work_dir.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self._lock_path), timeout=_LOCK_TIMEOUT):
            yield

    # ── Board YAML CRUD ──────────────────────────────────────────────────

    def _read_board_unlocked(self) -> dict:
        """Read board.yaml without acquiring the lock (caller must hold it)."""
        path = self.board_path
        if not path.exists():
            return {"counter": 0, "open": [], "done": []}
        try:
            data: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
            data.setdefault("open", [])
            data.setdefault("done", [])
            return data
        except Exception:
            logger.debug("read_board: failed to parse board file", path=str(path), exc_info=True)
            return {"counter": 0, "open": [], "done": []}

    def _write_board_unlocked(self, board: dict) -> None:
        """Write board.yaml atomically without acquiring the lock (caller must hold it)."""
        path = self.board_path
        path.parent.mkdir(parents=True, exist_ok=True)
        content = yaml.dump(board, default_flow_style=False, allow_unicode=True)
        tmp = path.with_suffix(".yaml.tmp")
        try:
            tmp.write_text(content)
            tmp.replace(path)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise

    def read_board(self) -> dict:
        with self._board_lock():
            return self._read_board_unlocked()

    def write_board(self, board: dict) -> None:
        with self._board_lock():
            self._write_board_unlocked(board)

    # ── Task status + comments ───────────────────────────────────────────

    def get_task(self, task_name: str) -> dict | None:
        with self._board_lock():
            board = self._read_board_unlocked()
        for entry in board.get("open", []):
            t = entry if isinstance(entry, dict) else {"name": str(entry)}
            if t.get("name") == task_name:
                return t
        return None

    def set_task_status(self, task_name: str, status: str) -> None:
        if status not in TASK_STATUSES:
            logger.warning("set_task_status: unknown status", status=status, task=task_name)
        with self._board_lock():
            board = self._read_board_unlocked()
            changed = False
            for i, entry in enumerate(board.get("open", [])):
                t = entry if isinstance(entry, dict) else {"name": str(entry)}
                if t.get("name") == task_name:
                    t["status"] = status
                    board["open"][i] = t
                    changed = True
                    break
            if changed:
                self._write_board_unlocked(board)
                logger.debug("task status updated", task=task_name, status=status)
            else:
                logger.warning("set_task_status: task not found", task=task_name)

    def add_task_comment(self, task_name: str, author: str, text: str) -> None:
        with self._board_lock():
            board = self._read_board_unlocked()
            changed = False
            for i, entry in enumerate(board.get("open", [])):
                t = entry if isinstance(entry, dict) else {"name": str(entry)}
                if t.get("name") == task_name:
                    comments: list[dict] = t.setdefault("comments", [])
                    comments.append(
                        {
                            "from": author,
                            "text": text,
                            "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        }
                    )
                    t["comments"] = comments
                    board["open"][i] = t
                    changed = True
                    break
            if changed:
                self._write_board_unlocked(board)
                logger.debug("task comment added", task=task_name, author=author)
            else:
                logger.warning("add_task_comment: task not found", task=task_name)

    # ── Task file CRUD ───────────────────────────────────────────────────

    def list_task_files(self) -> list[Path]:
        if not self._work_dir.is_dir():
            return []
        return sorted(p for p in self._work_dir.glob("*.md") if p.name.lower() != "readme.md")

    def delete_task_file(self, name: str) -> None:
        path = self._work_dir / name
        if path.exists():
            path.unlink()
            logger.info("deleted task file", path=str(path))
