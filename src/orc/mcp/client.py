"""HTTP client for the orc coordination API, used by MCP tool implementations.

Connects to the coordination server via the Unix domain socket path stored in
the ``ORC_API_SOCKET`` environment variable.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import httpx


def _get_socket_path() -> str:
    """Return the coordination API socket path from the environment.

    Raises
    ------
    RuntimeError
        If ``ORC_API_SOCKET`` is not set or the socket file does not exist.
    """
    path = os.environ.get("ORC_API_SOCKET", "").strip()
    if not path:
        raise RuntimeError(
            "ORC_API_SOCKET is not set. "
            "The orc MCP server must be launched by 'orc run' (not directly)."
        )
    if not Path(path).exists():
        raise RuntimeError(f"ORC_API_SOCKET={path!r} does not exist. Is 'orc run' still running?")
    return path


@contextmanager
def get_client() -> Generator[httpx.Client]:
    """Yield an ``httpx.Client`` connected to the orc coordination API."""
    socket_path = _get_socket_path()
    transport = httpx.HTTPTransport(uds=socket_path)
    with httpx.Client(transport=transport, base_url="http://orc", timeout=30.0) as client:
        yield client


def find_task_by_code(client: httpx.Client, code: str) -> str:
    """Return the task filename whose name starts with *code*.

    Parameters
    ----------
    client:
        Open ``httpx.Client`` connected to the coordination API.
    code:
        Four-digit zero-padded task number (e.g. ``"0002"``).

    Raises
    ------
    ValueError
        If no task matching *code* is found on the board.
    """
    resp = client.get("/board/tasks")
    resp.raise_for_status()
    tasks = resp.json()
    for task in tasks:
        name: str = task.get("name", "")
        if name.startswith(code):
            return name
    raise ValueError(f"No task found with code {code!r}. Check the board.")
