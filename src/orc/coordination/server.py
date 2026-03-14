"""Coordination server lifecycle — FastAPI on a Unix domain socket.

:class:`CoordinationServer` starts a uvicorn ASGI server in a background
daemon thread and exposes it over a Unix domain socket.  The server shares
state with the orchestrator's main thread via the injected
:class:`~orc.coordination.state.StateManager`.

Lifecycle
---------
1. ``CoordinationServer(state, socket_path).start()`` — creates the socket,
   starts uvicorn in a daemon thread, and waits up to
   :data:`_STARTUP_TIMEOUT` seconds for the server to report ``started``.
2. ``server.stop()`` — signals uvicorn to exit, waits for the thread to
   finish, then removes the socket file.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import structlog

from orc.coordination.state import StateManager

logger = structlog.get_logger(__name__)

_STARTUP_TIMEOUT: float = 10.0  # seconds to wait for uvicorn to become ready
_STARTUP_POLL: float = 0.05  # polling interval during startup


class CoordinationServer:
    """Manages a uvicorn/FastAPI server on a Unix domain socket.

    Parameters
    ----------
    state:
        The :class:`~orc.coordination.state.StateManager` instance to
        inject into every request handler.
    socket_path:
        Absolute path where the Unix domain socket will be created.
        Any existing file at that path is removed before binding.
    """

    def __init__(self, state: StateManager, socket_path: Path) -> None:
        self._state = state
        self._socket_path = socket_path
        self._server: object | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the coordination server in a background daemon thread.

        Raises :class:`RuntimeError` if uvicorn does not report ready
        within :data:`_STARTUP_TIMEOUT` seconds.
        """
        import uvicorn  # noqa: PLC0415

        from orc.coordination.app import create_app  # noqa: PLC0415

        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._socket_path.unlink(missing_ok=True)

        app = create_app(self._state)
        config = uvicorn.Config(
            app=app,
            uds=str(self._socket_path),
            log_level="warning",
            loop="asyncio",
            lifespan="off",
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run,
            daemon=True,
            name="orc-coordination-server",
        )
        self._thread.start()

        deadline = time.monotonic() + _STARTUP_TIMEOUT
        while not self._server.started and time.monotonic() < deadline:  # type: ignore[union-attr]
            time.sleep(_STARTUP_POLL)

        if not self._server.started:  # type: ignore[union-attr]
            self._thread.join(timeout=2.0)
            raise RuntimeError(
                f"Coordination server failed to start within {_STARTUP_TIMEOUT}s "
                f"on socket {self._socket_path}"
            )
        logger.info("coordination server started", socket=str(self._socket_path))

    def stop(self) -> None:
        """Shut down the server and remove the socket file."""
        if self._server is not None:
            self._server.should_exit = True  # type: ignore[union-attr]
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._socket_path.unlink(missing_ok=True)
        logger.info("coordination server stopped")
