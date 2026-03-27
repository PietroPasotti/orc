"""orc MCP tools — board operations used by agents and orchestrator operations.

Tool functions live in :mod:`orc.mcp.tools` and are called in-process by the
agent's :class:`~orc.ai.tools.ToolExecutor`.  The HTTP client in
:mod:`orc.mcp.client` communicates with the coordination API over a Unix
domain socket.

Environment variables (set by the orchestrator before spawning agents):

    ORC_API_SOCKET   Path to the orc coordination API Unix domain socket.
    ORC_AGENT_ID     Agent identifier (e.g. ``coder-1``).
    ORC_AGENT_ROLE   Agent role (e.g. ``coder``).
"""
