"""Pydantic request/response models for the orc coordination API."""

from __future__ import annotations

from pydantic import BaseModel


class CreateTaskRequest(BaseModel):
    """Body for ``POST /board/tasks``."""

    title: str
    """Short dash-separated task title, e.g. ``add-user-auth``."""


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


class TaskEntry(BaseModel):
    """A single task entry on the kanban board."""

    name: str
    status: str | None = None
    assigned_to: str | None = None
    comments: list[dict] = []


class HealthResponse(BaseModel):
    """Response from ``GET /health``."""

    status: str
    pid: int
