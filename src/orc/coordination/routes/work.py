"""Work-snapshot route for the orc coordination API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from orc.coordination.state import StateManager

router = APIRouter(tags=["work"])


def _get_state(request: Request) -> StateManager:
    return request.app.state.coord_state  # type: ignore[no-any-return]


@router.get("/health")
def health(state: StateManager = Depends(_get_state)) -> dict:
    """Liveness probe — returns ``{"status": "ok"}``."""
    import os  # noqa: PLC0415

    _ = state  # accessed only to confirm server is up and state is present
    return {"status": "ok", "pid": os.getpid()}
