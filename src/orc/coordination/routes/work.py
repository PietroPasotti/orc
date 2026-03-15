"""Work-snapshot route for the orc coordination API."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request

from orc.coordination.models import HealthResponse
from orc.coordination.state import BoardStateManager

router = APIRouter(tags=["work"])


def _get_state(request: Request) -> BoardStateManager:
    return request.app.state.coord_state  # type: ignore[no-any-return]


@router.get("/health", response_model=HealthResponse)
def health(state: BoardStateManager = Depends(_get_state)) -> HealthResponse:
    """Liveness probe — returns ``{"status": "ok", "pid": <pid>}``."""
    _ = state  # accessed only to confirm server is up and state is present
    return HealthResponse(status="ok", pid=os.getpid())
