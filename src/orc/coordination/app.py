"""FastAPI application factory for the orc coordination API."""

from __future__ import annotations

from fastapi import FastAPI

from orc.coordination.routes import board, visions, work
from orc.coordination.state import BoardStateManager


def create_app(state: BoardStateManager) -> FastAPI:
    """Create and return a configured FastAPI app backed by *state*.

    The *state* manager is stored on ``app.state.coord_state`` and injected
    into every route handler via FastAPI's dependency system.
    """
    app = FastAPI(
        title="orc coordination API",
        description=(
            "Single source of truth for board and vision state. "
            "All agent tools read/write through this API."
        ),
        version="1.0",
        docs_url=None,
        redoc_url=None,
    )
    app.state.coord_state = state
    app.include_router(board.router)
    app.include_router(visions.router)
    app.include_router(work.router)
    return app
