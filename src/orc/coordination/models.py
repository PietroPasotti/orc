"""Pydantic request/response models for the orc coordination API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Board domain types
# ---------------------------------------------------------------------------


class TaskComment(BaseModel):
    """A comment attached to a board task entry."""

    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    """Agent or user that added this comment."""
    text: str
    """Comment body."""
    ts: str = ""
    """ISO-8601 timestamp."""


class TaskEntry(BaseModel):
    """A single task entry on the kanban board."""

    name: str
    status: str | None = None
    assigned_to: str | None = None
    comments: list[TaskComment] = []
    commit_tag: str | None = None
    timestamp: str | None = None


class Board(BaseModel):
    """Top-level board.yaml structure."""

    counter: int = 0
    tasks: list[TaskEntry] = []


# ---------------------------------------------------------------------------
# Task-file body (used in create-task requests and board manager)
# ---------------------------------------------------------------------------


class TaskBody(BaseModel):
    """Structured content for a task markdown file."""

    overview: str
    """Free-form description of what is being built and why."""
    in_scope: list[str]
    """Items explicitly in scope for this task."""
    out_of_scope: list[str]
    """Items explicitly out of scope for this task."""
    steps: list[str]
    """Ordered implementation steps."""
    notes: str = ""
    """Optional free-form notes: blockers, design decisions, tips for the coder."""


# ---------------------------------------------------------------------------
# HTTP request / response models
# ---------------------------------------------------------------------------


class CreateTaskRequest(BaseModel):
    """Body for ``POST /board/tasks``."""

    title: str
    """Short dash-separated task title, e.g. ``add-user-auth``."""
    vision: str
    """Filename of the vision this task was refined from, e.g. ``0001-shark-fleet.md``."""
    body: TaskBody
    """Structured task content assembled into the markdown file by the server."""


class CreateTaskResponse(BaseModel):
    """Response from ``POST /board/tasks``."""

    filename: str
    """Created task filename, e.g. ``0003-add-user-auth.md``."""
    path: str
    """Absolute filesystem path to the created task file."""


class SetStatusRequest(BaseModel):
    """Body for ``PUT /board/tasks/{name}/status``."""

    status: str
    """New task status — must be one of the valid TASK_STATUSES."""


class AddCommentRequest(BaseModel):
    """Body for ``POST /board/tasks/{name}/comments``."""

    author: str
    """Agent ID that is adding the comment, e.g. ``qa-1``."""
    text: str
    """Comment text."""


class CloseVisionRequest(BaseModel):
    """Body for ``POST /visions/{name}/close``."""

    summary: str
    """2–4 sentence summary of what the vision described."""
    task_files: list[str] = []
    """Optional list of task filenames that implemented this vision."""


class TaskContent(BaseModel):
    """Response from ``GET /board/tasks/{name}/content``."""

    name: str
    """Task filename."""
    content: str
    """Raw markdown content of the task file."""


class VisionContent(BaseModel):
    """Response from ``GET /visions/{name}``."""

    name: str
    """Vision filename."""
    content: str
    """Raw markdown content of the vision file."""


class OkResponse(BaseModel):
    """Generic success acknowledgement."""

    ok: bool = True


class HealthResponse(BaseModel):
    """Response from ``GET /health``."""

    status: str
    pid: int
