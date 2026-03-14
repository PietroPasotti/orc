"""Thread-safe state manager for the orc coordination API.

:class:`StateManager` wraps :class:`~orc.board_manager.FileBoardManager`
with a :class:`threading.RLock` so that concurrent HTTP request handlers
(running in anyio's thread pool) and the orchestrator's main thread can
safely share board and vision state.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path

import structlog

from orc.board_manager import FileBoardManager

logger = structlog.get_logger(__name__)


def _locked[**P, R](method: Callable[P, R]) -> Callable[P, R]:
    """Acquire ``self._lock`` around *method* and return its result."""

    @wraps(method)
    def _wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        self = args[0]
        with self._lock:  # type: ignore[attr-defined]
            return method(*args, **kwargs)

    return _wrapper  # type: ignore[return-value]


class StateManager:
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
    def get_open_tasks(self) -> list[dict]:
        """Return the open-tasks list from board.yaml."""
        board = self._mgr.read_board()
        result = []
        for t in board.get("open", []):
            result.append(t if isinstance(t, dict) else {"name": str(t)})
        return result

    @_locked
    def get_done_tasks(self) -> list[dict]:
        """Return the done-tasks list from board.yaml."""
        board = self._mgr.read_board()
        result = []
        for t in board.get("done", []):
            entry: dict = dict(t) if isinstance(t, dict) else {"name": str(t)}
            # Normalise the hyphenated YAML key to the model's field name.
            if "commit-tag" in entry:
                entry["commit_tag"] = entry.pop("commit-tag")
            result.append(entry)
        return result

    @_locked
    def get_all_tasks(self) -> list[dict]:
        """Return all tasks (open + done) from board.yaml."""
        board = self._mgr.read_board()
        result = []
        for t in board.get("open", []):
            result.append(t if isinstance(t, dict) else {"name": str(t)})
        for t in board.get("done", []):
            result.append(t if isinstance(t, dict) else {"name": str(t)})
        return result

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
        for t in board.get("open", []):
            if isinstance(t, dict) and t.get("name") == task_name:
                t["assigned_to"] = agent_id
                if t.get("status") in (None, "", "planned", "rejected"):
                    t["status"] = "coding"
                self._mgr.write_board(board)
                return
        logger.warning("assign_task: task not found", task=task_name)

    @_locked
    def unassign_task(self, task_name: str) -> None:
        """Clear the ``assigned_to`` field for *task_name* in board.yaml."""
        board = self._mgr.read_board()
        changed = False
        for t in board.get("open", []):
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
        for t in board.get("open", []):
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
        """Return vision ``.md`` filenames that have no matching board task."""
        vision_dir = self._mgr.vision_dir
        if not vision_dir.is_dir():
            return []
        board = self._mgr.read_board()
        all_task_stems = {
            (t["name"] if isinstance(t, dict) else str(t))
            for tasks in (board.get("open", []), board.get("done", []))
            for t in tasks
        }
        result = []
        for f in sorted(vision_dir.glob("*.md")):
            if f.name.lower().startswith(".") or f.name.lower() == "readme.md":
                continue
            if not any(stem == f.name or stem.startswith(f.stem) for stem in all_task_stems):
                result.append(f.name)
        return result

    def read_vision(self, name: str) -> str:
        """Return the content of a vision file.

        Raises :class:`FileNotFoundError` if *name* is not found.
        """
        vision_path = self._mgr.vision_dir / name
        if not vision_path.exists():
            raise FileNotFoundError(f"Vision not found: {name}")
        return vision_path.read_text()

    # FIXME: close_vision should not be editing the changelog.
    #  Changelog should be an append-only log of feature branches merged into dev (and then main).
    #  so probably it's more of a finalize_task side-effect? Whenever the task branch is
    #  merged on dev, that's when we should add an entry to the changelog.
    @_locked
    def close_vision(self, name: str, summary: str, task_files: list[str]) -> None:
        """Close a vision: append an entry to ``orc-CHANGELOG.md`` and delete the file.

        Raises :class:`FileNotFoundError` if *name* is not found.
        """
        vision_path = self._mgr.vision_dir / name
        if not vision_path.exists():
            raise FileNotFoundError(f"Vision not found: {name}")
        vision_name = vision_path.stem
        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        implemented_by = ", ".join(task_files) if task_files else "—"
        entry = (
            f"\n## {vision_name} (closed {timestamp})\n\n"
            f"**Summary:** {summary}\n\n"
            f"**Implemented by:** {implemented_by}\n"
        )
        changelog = self._orc_dir / "orc-CHANGELOG.md"
        if changelog.exists():
            changelog.write_text(changelog.read_text() + entry)
        else:
            changelog.write_text(f"# Changelog\n{entry}")
        # don't unlink vision path; instead move it to an 'old' subdir for record-keeping

        done_dir = self._mgr.vision_dir / "old"
        done_dir.mkdir(exist_ok=True)
        vision_path.rename(done_dir / name)

        logger.info("closed vision", name=name)
