"""Board CRUD routes for the orc coordination API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

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


# TODO: generalize this by adding a {status} path param and supporting
#  filtering by status, e.g. /board/tasks?status=open
#  that way, we can simplify the board data structure and let it be a flat list of Task
#  objects with a 'status' field that can be set to 'planned', 'in-progress', 'review', 'closed',
#  etc. instead of having 2 separate lists for open & everything else.
@router.get("/tasks", response_model=list[TaskEntry])
def get_tasks(state: StateManager = Depends(_get_state)) -> list[dict]:
    """Return all open tasks from board.yaml."""
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
    filename, path = state.create_task(body.title, body.vision, body.body.model_dump())
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
