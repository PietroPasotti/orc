"""Lightweight HTTP client for the orc coordination API.

This module provides a small helper used by the TUI and other internal
consumers to talk to the coordination server over its Unix-domain socket
(``ORC_API_SOCKET`` env var).

Usage::

    from orc.coordination.client import get_board_snapshot, BoardSnapshot

    snap = get_board_snapshot()
    if snap is None:
        # server unreachable
        ...
    else:
        # snap.visions, snap.tasks
        ...
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import httpx


@dataclass
class BoardSnapshot:
    """Aggregated kanban data fetched from the coordination API."""

    visions: list[str] = field(default_factory=list)
    """Vision filenames pending refinement (``GET /visions``)."""

    tasks: list[dict] = field(default_factory=list)
    """Active task entries from ``GET /board/tasks``."""


def get_board_snapshot() -> BoardSnapshot | None:
    """Fetch a :class:`BoardSnapshot` from the coordination API.

    Returns ``None`` when the server socket is absent or unreachable so
    that callers can degrade gracefully without crashing.
    """
    socket_path = os.environ.get("ORC_API_SOCKET", "")
    if not socket_path:
        return None

    transport = httpx.HTTPTransport(uds=socket_path)
    try:
        with httpx.Client(transport=transport, base_url="http://orc") as client:
            visions_resp = client.get("/visions")
            tasks_resp = client.get("/board/tasks")
    except Exception:
        return None

    try:
        visions: list[str] = visions_resp.json()
        tasks: list[dict] = tasks_resp.json()
    except Exception:
        return None

    return BoardSnapshot(visions=visions, tasks=tasks)
