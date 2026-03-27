"""Board CRUD routes for the orc coordination API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from orc.coordination.models import (
    AddCommentRequest,
    CreateTaskRequest,
    CreateTaskResponse,
    OkResponse,
    SetStatusRequest,
    TaskContent,
    TaskEntry,
)
from orc.coordination.state import BoardStateManager

router = APIRouter(prefix="/board", tags=["board"])


def _get_state(request: Request) -> BoardStateManager:
    return request.app.state.coord_state  # type: ignore[no-any-return]


@router.get("/tasks", response_model=list[TaskEntry])
def get_tasks(
    state: BoardStateManager = Depends(_get_state),
    status_filter: str | None = Query(
        None,
        alias="status",
        description="Filter tasks by status (e.g. planned, in-progress, in-review, done, blocked).",
    ),
) -> list[TaskEntry]:
    """Return tasks from board.yaml.

    - No filter (default) — all active tasks.
    - ``status=<value>`` — tasks with that exact status value.
    """
    tasks = state.get_tasks()
    if status_filter is not None:
        tasks = [t for t in tasks if t.status == status_filter]
    return tasks


@router.get("/tasks/{task_name:path}/content", response_model=TaskContent)
def get_task_content(task_name: str, state: BoardStateManager = Depends(_get_state)) -> TaskContent:
    """Return the raw markdown content of a task file by exact filename."""
    try:
        content = state.read_task_content(task_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Task file not found: {task_name}")
    return TaskContent(name=task_name, content=content)


@router.get("/tasks/{task_name:path}", response_model=TaskEntry)
def get_task(task_name: str, state: BoardStateManager = Depends(_get_state)) -> TaskEntry:
    """Return a single task entry by exact filename."""
    task = state.get_task(task_name)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_name}")
    return task


@router.post("/tasks", response_model=CreateTaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    body: CreateTaskRequest, state: BoardStateManager = Depends(_get_state)
) -> CreateTaskResponse:
    """Create a new task file and board entry."""
    filename, path = state.create_task(body.title, body.vision, body.body)
    return CreateTaskResponse(filename=filename, path=str(path))


@router.put("/tasks/{task_name:path}/status", status_code=status.HTTP_204_NO_CONTENT)
def set_status(
    task_name: str,
    body: SetStatusRequest,
    state: BoardStateManager = Depends(_get_state),
) -> None:
    """Set the status of a task."""
    state.set_task_status(task_name, body.status)


@router.delete("/tasks/{task_name:path}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_name: str,
    state: BoardStateManager = Depends(_get_state),
) -> None:
    """Remove a task from board.yaml and delete its task file."""
    state.delete_task(task_name)


@router.post(
    "/tasks/{task_name:path}/comments",
    response_model=OkResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_comment(
    task_name: str,
    body: AddCommentRequest,
    state: BoardStateManager = Depends(_get_state),
) -> OkResponse:
    """Append a comment to a task's comments list."""
    state.add_task_comment(task_name, body.author, body.text)
    return OkResponse()
