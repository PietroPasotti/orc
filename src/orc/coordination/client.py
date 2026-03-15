"""Lightweight HTTP client for the orc coordination API.

This module provides a small helper used by the TUI and other internal
consumers to talk to the coordination server over its Unix-domain socket.
The socket path is resolved via :func:`orc.config.get`.

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

from dataclasses import dataclass, field

import httpx

from orc.coordination.models import TaskEntry


@dataclass
class BoardSnapshot:
    """Aggregated kanban data fetched from the coordination API."""

    visions: list[str] = field(default_factory=list)
    """Vision filenames pending refinement (``GET /visions``)."""

    tasks: list[TaskEntry] = field(default_factory=list)
    """Active task entries from ``GET /board/tasks``."""


def get_board_snapshot() -> BoardSnapshot | None:
    """Fetch a :class:`BoardSnapshot` from the coordination API.

    Returns ``None`` when the server socket is absent or unreachable so
    that callers can degrade gracefully without crashing.
    """
    try:
        import orc.config as _cfg  # noqa: PLC0415

        socket_path = str(_cfg.get().api_socket_path)
    except Exception:
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
        tasks_raw: list[object] = tasks_resp.json()
        tasks = [TaskEntry.model_validate(t) for t in tasks_raw if isinstance(t, dict)]
    except Exception:
        return None

    return BoardSnapshot(visions=visions, tasks=tasks)
