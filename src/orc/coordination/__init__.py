"""orc coordination API — single source of truth for board and vision state.

The coordination API server runs as a background thread inside the ``orc run``
process and exposes a FastAPI application over a Unix domain socket.  All agent
tools that need to read or write board / vision state connect to this socket
instead of touching the filesystem directly.

Public surface
--------------
:class:`~orc.coordination.state.StateManager`
    Thread-safe wrapper around :class:`~orc.board_manager.FileBoardManager`
    that handles all board and vision mutations.

:class:`~orc.coordination.server.CoordinationServer`
    Manages the uvicorn server lifecycle (start / stop on Unix socket).

:func:`~orc.coordination.app.create_app`
    FastAPI application factory; takes a :class:`~orc.coordination.state.StateManager`
    and returns a configured :class:`fastapi.FastAPI` instance.
"""

from orc.coordination.server import CoordinationServer
from orc.coordination.state import StateManager

__all__ = ["CoordinationServer", "StateManager"]
