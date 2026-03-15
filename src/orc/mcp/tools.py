"""MCP tool implementations for the orc board operations.

Each function in this module corresponds to one MCP tool.  They are registered
against the FastMCP server in :mod:`orc.mcp.server` with role-based filtering.

All tools that perform git commits set ``GIT_AUTHOR_NAME`` / ``GIT_COMMITTER_NAME``
from the ``ORC_AGENT_ID`` environment variable, matching the behaviour of the
old ``agent_tools`` Python scripts.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from orc.mcp.client import find_task_by_code, get_client


def _agent_id() -> str:
    return os.environ.get("ORC_AGENT_ID", "unknown-agent")


def _git_author_env() -> dict[str, str]:
    """Return env overrides so git commits carry the agent's identity."""
    agent = _agent_id()
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = agent
    env["GIT_AUTHOR_EMAIL"] = f"{agent}@orc.local"
    env["GIT_COMMITTER_NAME"] = agent
    env["GIT_COMMITTER_EMAIL"] = f"{agent}@orc.local"
    return env


def _run_git(*args: str) -> None:
    """Run a git command in the current working directory, raising on failure."""
    result = subprocess.run(["git", *args], env=_git_author_env(), capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {result.returncode}):\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Shared tools (available to all roles)
# ---------------------------------------------------------------------------


def get_task(task_filename: str) -> str:
    """Fetch a task's markdown content and conversation thread.

    Parameters
    ----------
    task_filename:
        Full task filename, e.g. ``"0003-add-user-auth.md"``.

    Returns
    -------
    str
        Markdown content followed by a ``## Conversation`` section.
    """
    with get_client() as client:
        content_resp = client.get(f"/board/tasks/{task_filename}/content")
        if content_resp.status_code == 404:
            raise ValueError(f"Task {task_filename!r} not found on the board.")
        content_resp.raise_for_status()
        content: str = content_resp.json().get("content", "")

        meta_resp = client.get(f"/board/tasks/{task_filename}")
        meta_resp.raise_for_status()
        meta = meta_resp.json()

    comments = meta.get("comments") or []
    conversation = "\n\n## Conversation\n\n"
    if comments:
        for c in comments:
            conversation += f"**{c.get('from', '?')}** _{c.get('ts', '')}_\n{c.get('text', '')}\n\n"
    else:
        conversation += "_No comments yet._\n"

    return content + conversation


def update_task_status(task_code: str, status: str) -> str:
    """Change a task's status on the board.

    Parameters
    ----------
    task_code:
        Four-digit zero-padded task number, e.g. ``"0002"``.
    status:
        New status: ``planned``, ``in-progress``, ``in-review``, ``done``,
        ``blocked``, or ``stuck``.

    Returns
    -------
    str
        Confirmation message.
    """
    valid = {"planned", "in-progress", "in-review", "done", "blocked", "stuck"}
    if status not in valid:
        raise ValueError(f"Invalid status {status!r}. Valid values: {', '.join(sorted(valid))}")
    with get_client() as client:
        task_name = find_task_by_code(client, task_code)
        resp = client.put(f"/board/tasks/{task_name}/status", json={"status": status})
        resp.raise_for_status()
    return f"Task {task_name} status set to {status!r}."


def add_comment(task_code: str, comment: str) -> str:
    """Append a comment to a task's conversation thread.

    Parameters
    ----------
    task_code:
        Four-digit zero-padded task number, e.g. ``"0002"``.
    comment:
        Comment text to append.

    Returns
    -------
    str
        Confirmation message.
    """
    with get_client() as client:
        task_name = find_task_by_code(client, task_code)
        resp = client.post(
            f"/board/tasks/{task_name}/comments",
            json={"author": _agent_id(), "text": comment},
        )
        resp.raise_for_status()
    return f"Comment added to {task_name}."


# ---------------------------------------------------------------------------
# Planner tools
# ---------------------------------------------------------------------------


def get_vision(vision_filename: str) -> str:
    """Fetch the content of a vision document.

    Parameters
    ----------
    vision_filename:
        Vision filename, e.g. ``"0001-shark-fleet.md"``.

    Returns
    -------
    str
        Raw markdown content of the vision.
    """
    with get_client() as client:
        resp = client.get(f"/visions/{vision_filename}")
        if resp.status_code == 404:
            raise ValueError(f"Vision {vision_filename!r} not found.")
        resp.raise_for_status()
        return str(resp.json().get("content", ""))


def create_task(
    task_title: str,
    vision_file: str,
    overview: str,
    in_scope: list[str],
    out_of_scope: list[str],
    steps: list[str],
    notes: str = "",
    extra_files: list[str] | None = None,
) -> str:
    """Create a new task on the board and commit any extra files.

    Parameters
    ----------
    task_title:
        Dash-separated title, e.g. ``"add-user-auth"``.
    vision_file:
        Source vision filename, e.g. ``"0001-auth-vision.md"``.
    overview:
        Short description of what and why.
    in_scope:
        List of items explicitly in scope.
    out_of_scope:
        List of items explicitly out of scope.
    steps:
        Ordered list of implementation steps.
    notes:
        Optional free-form notes.
    extra_files:
        Optional list of file paths to stage and commit alongside the task
        (e.g. ADR documents).

    Returns
    -------
    str
        The filename of the created task (e.g. ``"0003-add-user-auth.md"``).
    """
    body = {
        "overview": overview,
        "in_scope": in_scope,
        "out_of_scope": out_of_scope,
        "steps": steps,
        "notes": notes,
    }
    with get_client() as client:
        resp = client.post(
            "/board/tasks",
            json={"title": task_title, "vision": vision_file, "body": body},
        )
        resp.raise_for_status()
        task_filename: str = resp.json()["filename"]

    if extra_files:
        for f in extra_files:
            _run_git("add", "--", f)

    commit_msg = f"chore({_agent_id()}): add task {Path(task_filename).stem}"
    _run_git("commit", "--allow-empty", "-m", commit_msg)

    return task_filename


def close_vision(vision_file: str, summary: str, task_files: list[str] | None = None) -> str:
    """Mark a vision as complete, moving it to the done archive.

    Parameters
    ----------
    vision_file:
        Vision filename, e.g. ``"0001-shark-fleet.md"``.
    summary:
        2–4 sentence description of what was accomplished.
    task_files:
        Optional list of task filenames that implement this vision.

    Returns
    -------
    str
        Confirmation message.
    """
    vision_name = Path(vision_file).name
    with get_client() as client:
        resp = client.post(
            f"/visions/{vision_name}/close",
            json={"summary": summary, "task_files": task_files or []},
        )
        resp.raise_for_status()
    return f"Closed vision: {vision_name}"


# ---------------------------------------------------------------------------
# Coder tools
# ---------------------------------------------------------------------------


def close_task(task_code: str, message: str) -> str:
    """Signal that implementation is complete for a task.

    Stages all changes, commits with a ``feat(<code>)`` message, and sets the
    board status to ``in-review``.

    Parameters
    ----------
    task_code:
        Four-digit zero-padded task number, e.g. ``"0002"``.
    message:
        Short description of what was implemented.

    Returns
    -------
    str
        Confirmation message.
    """
    _run_git("add", "-A")
    commit_msg = f"feat({task_code}): {message}"
    _run_git("commit", "--allow-empty", "-m", commit_msg)

    with get_client() as client:
        task_name = find_task_by_code(client, task_code)
        resp = client.put(f"/board/tasks/{task_name}/status", json={"status": "in-review"})
        resp.raise_for_status()

    return f"Task {task_name} closed and set to in-review."


# ---------------------------------------------------------------------------
# QA tools
# ---------------------------------------------------------------------------


def review_task(task_code: str, outcome: str, message: str) -> str:
    """Signal the outcome of a QA review.

    Commits any staged changes, updates the board status, and (on rejection)
    appends a comment with the rejection reason.

    Parameters
    ----------
    task_code:
        Four-digit zero-padded task number, e.g. ``"0002"``.
    outcome:
        ``"done"`` to approve, ``"in-progress"`` to reject and send back to coder.
    message:
        Summary of the review outcome (reason for rejection if applicable).

    Returns
    -------
    str
        Confirmation message.
    """
    if outcome not in ("done", "in-progress"):
        raise ValueError(
            f"Invalid outcome {outcome!r}. Use 'done' to approve or 'in-progress' to reject."
        )

    verdict = "approved" if outcome == "done" else "rejected"
    commit_msg = f"chore(qa/{task_code}): {verdict} — {message}"
    _run_git("commit", "-a", "--allow-empty", "-m", commit_msg)

    with get_client() as client:
        task_name = find_task_by_code(client, task_code)
        status_resp = client.put(f"/board/tasks/{task_name}/status", json={"status": outcome})
        status_resp.raise_for_status()

        if outcome == "in-progress":
            comment_resp = client.post(
                f"/board/tasks/{task_name}/comments",
                json={"author": _agent_id(), "text": message},
            )
            comment_resp.raise_for_status()

    return f"Review complete: task {task_name} {verdict}."
