"""Vision routes for the orc coordination API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from orc.coordination.models import CloseVisionRequest
from orc.coordination.state import BoardStateManager

router = APIRouter(prefix="/visions", tags=["visions"])


def _get_state(request: Request) -> BoardStateManager:
    return request.app.state.coord_state  # type: ignore[no-any-return]


@router.get("", response_model=list[str])
def get_pending_visions(state: BoardStateManager = Depends(_get_state)) -> list[str]:
    """Return vision filenames that have no matching board task."""
    return state.get_pending_visions()


@router.get("/{name:path}")
def get_vision(name: str, state: BoardStateManager = Depends(_get_state)) -> dict:
    """Return the content of a vision file."""
    try:
        content = state.read_vision(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Vision not found: {name}")
    return {"name": name, "content": content}


@router.post("/{name:path}/close", status_code=status.HTTP_204_NO_CONTENT)
def close_vision(
    name: str,
    body: CloseVisionRequest,
    state: BoardStateManager = Depends(_get_state),
) -> None:
    """Close a vision: append changelog entry and delete the vision file."""
    try:
        state.close_vision(name, body.summary, body.task_files)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Vision not found: {name}")
