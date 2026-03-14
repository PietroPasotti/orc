"""HTTP client for the orc coordination API over a Unix domain socket.

Usage in agent tools::

    from _orc_client import get_client, find_task_by_code

    with get_client() as client:
        resp = client.put(f"/board/tasks/{name}/status", json={"status": "review"})
        resp.raise_for_status()

The socket path is read from the ``ORC_API_SOCKET`` environment variable,
which ``orc run`` sets before spawning agent subprocesses.  Agent tools MUST
run inside ``orc run`` — direct filesystem access to ``.orc/`` is forbidden
because the board may be in a git worktree that has its own ``.orc/`` copy.
"""

from __future__ import annotations

import os
from pathlib import Path

# httpx is a runtime dep of orc; agent tools inherit the venv.
import httpx

_BASE_URL = "http://orc"  # dummy hostname; routing is via the Unix socket


def get_client() -> httpx.Client:
    """Return an ``httpx.Client`` connected to the coordination API socket.

    Raises :class:`RuntimeError` when ``ORC_API_SOCKET`` is not set or the
    socket file does not exist — agent tools must run inside ``orc run``.
    """
    socket_path = os.environ.get("ORC_API_SOCKET", "")
    if not socket_path or not Path(socket_path).exists():
        raise RuntimeError(
            "ORC_API_SOCKET is not set or the socket file does not exist. "
            "Agent tools must be run inside 'orc run'."
        )
    return httpx.Client(
        transport=httpx.HTTPTransport(uds=socket_path),
        base_url=_BASE_URL,
        timeout=30.0,
    )


def find_task_by_code(client: httpx.Client, code: str) -> str | None:
    """Find a task filename whose name starts with the 4-digit *code*.

    Returns the task filename (e.g. ``"0003-add-user-auth.md"``) or ``None``
    if no matching task is found on the board.
    """
    resp = client.get("/board/tasks")
    resp.raise_for_status()
    for task in resp.json():
        if task.get("name", "").startswith(code):
            return task["name"]
    return None
