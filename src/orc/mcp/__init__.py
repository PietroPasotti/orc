"""orc MCP server — exposes orc board operations as MCP tools.

Agents connect to this server (via stdio) to interact with the orc coordination
API without needing to shell out to Python scripts.  The server is role-aware:
only tools appropriate for the agent's role are registered.

Usage::

    python -m orc.mcp

Environment variables (required at startup):

    ORC_API_SOCKET   Path to the orc coordination API Unix domain socket.
    ORC_AGENT_ID     Agent identifier (e.g. ``coder-1``).
    ORC_AGENT_ROLE   Agent role: ``planner``, ``coder``, or ``qa``.
"""
