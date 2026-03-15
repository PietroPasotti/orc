"""MCP server setup and tool registration for the orc orchestrator.

Role-based filtering
---------------------
The server reads ``ORC_AGENT_ROLE`` at startup and registers only the tools
appropriate for that role, plus the shared tools available to all roles.

Roles and their tools
~~~~~~~~~~~~~~~~~~~~~

+----------+----------------------------------------------------+
| Role     | Tools                                              |
+==========+====================================================+
| all      | ``get_task``, ``update_task_status``, ``add_comment`` |
+----------+----------------------------------------------------+
| planner  | ``get_vision``, ``create_task``, ``close_vision``  |
+----------+----------------------------------------------------+
| coder    | ``close_task``                                     |
+----------+----------------------------------------------------+
| qa       | ``review_task``                                    |
+----------+----------------------------------------------------+
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

import orc.mcp.tools as _tools
from orc.squad import AgentRole

_VALID_ROLES = frozenset({"planner", "coder", "qa"})


def _get_role() -> AgentRole:
    """Return the agent role from ``ORC_AGENT_ROLE``.

    Falls back to ``"coder"`` (most restrictive) rather than failing loudly, so
    that ad-hoc testing (e.g. ``python -m orc.mcp``) still produces a usable
    server.
    """
    role = os.environ.get("ORC_AGENT_ROLE", "").strip().lower()
    if role not in _VALID_ROLES:
        raise ValueError(
            f"Invalid ORC_AGENT_ROLE: {role!r}. Must be one of {', '.join(_VALID_ROLES)}."
        )
    return AgentRole(role)


def _build_server() -> FastMCP:
    """Construct a :class:`FastMCP` instance with role-filtered tools."""
    role = _get_role()
    mcp: FastMCP = FastMCP(
        name="orc",
        instructions=(
            "You are interacting with the orc orchestrator board.\n"
            "Use these tools to read and update task state instead of touching "
            ".orc/ files directly."
        ),
    )

    # Shared tools — available to every role.
    mcp.tool()(_tools.get_task)
    mcp.tool()(_tools.update_task_status)
    mcp.tool()(_tools.add_comment)

    # Role-specific tools.
    match role:
        case AgentRole.PLANNER:
            mcp.tool()(_tools.get_vision)
            mcp.tool()(_tools.create_task)
            mcp.tool()(_tools.close_vision)
        case AgentRole.CODER:
            mcp.tool()(_tools.close_task)
        case AgentRole.QA:
            mcp.tool()(_tools.review_task)

    return mcp


def run() -> None:
    """Start the MCP server (stdio transport)."""
    server = _build_server()
    server.run(transport="stdio")
