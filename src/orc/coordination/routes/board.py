"""Board CRUD routes for the orc coordination API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from orc.coordination.models import (
    AddCommentRequest,
    CreateTaskRequest,
    CreateTaskResponse,
    SetStatusRequest,
    TaskEntry,
)
from orc.coordination.state import StateManager

router = APIRouter(prefix="/board", tags=["board"])


def _get_state(request: Request) -> StateManager:
    return request.app.state.coord_state  # type: ignore[no-any-return]


@router.get("/tasks", response_model=list[TaskEntry])
def get_tasks(
    state: StateManager = Depends(_get_state),
    status_filter: str | None = Query(
        None,
        alias="status",
        description="Filter tasks by status bucket: open (default), done, or all.",
    ),
) -> list[dict]:
    """Return tasks from board.yaml.

    - ``status=open`` (default, omitted) — open tasks only.
    - ``status=done`` — done tasks only.
    - ``status=all`` — open and done tasks combined.
    """
    if status_filter == "done":
        return state.get_done_tasks()
    if status_filter == "all":
        return state.get_all_tasks()
    return state.get_open_tasks()


@router.get("/tasks/{task_name:path}", response_model=TaskEntry)
def get_task(task_name: str, state: StateManager = Depends(_get_state)) -> dict:
    """Return a single task entry by exact filename."""
    task = state.get_task(task_name)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_name}")
    return task


@router.post("/tasks", response_model=CreateTaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(body: CreateTaskRequest, state: StateManager = Depends(_get_state)) -> dict:
    """Create a new task file and board entry."""
    filename, path = state.create_task(body.title)
    return {"filename": filename, "path": str(path)}


@router.put("/tasks/{task_name:path}/status", status_code=status.HTTP_204_NO_CONTENT)
def set_status(
    task_name: str,
    body: SetStatusRequest,
    state: StateManager = Depends(_get_state),
) -> None:
    """Set the status of a task."""
    state.set_task_status(task_name, body.status)


@router.post(
    "/tasks/{task_name:path}/comments",
    status_code=status.HTTP_201_CREATED,
)
def add_comment(
    task_name: str,
    body: AddCommentRequest,
    state: StateManager = Depends(_get_state),
) -> dict:
    """Append a comment to a task's comments list."""
    state.add_task_comment(task_name, body.author, body.text)
    return {"ok": True}
