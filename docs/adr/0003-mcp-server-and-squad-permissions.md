# ADR-0003: MCP Server and Squad Permissions

**Status:** Superseded  
**Date:** 2025  
**Superseded by:** ADR-0005 (Workflow Engine)  
**Context:** Replacing `agent_tools` Python scripts with an MCP server; adding per-squad tool permission configuration.

> **Note (2026-03):** The MCP server architecture described here has been
> superseded by the workflow engine refactor.  Agents no longer spawn a
> separate MCP server subprocess — ORC tools are called in-process by the
> `ToolExecutor`.  Only the **coder** role is spawned as an agent; planner,
> QA, and merger are now orchestrator operations (single LLM calls).
> The permission model and squad configuration described here remain in use
> for coder agents.  References to "Copilot CLI" and "Claude CLI" backends
> are historical.

---

## Context

Agents previously interacted with the orc coordination API by shelling out to Python scripts located at `.orc/agent_tools/<role>/<script>.py`. These scripts were injected into the agent context as file paths and executed via the agent's shell tool access, which required:

1. `--yolo` mode on every agent invocation (unrestricted tool access)
2. Agents having a valid Python environment with `httpx` installed
3. Complex context instructions explaining script paths and calling conventions
4. Error handling via exit codes and stderr text

This pattern is fragile and prevents running agents in a security-constrained mode.

---

## Decision

### 1. Replace `agent_tools` with an MCP server

The `orc.mcp` package implements a [Model Context Protocol](https://modelcontextprotocol.io/) server that exposes board operations as first-class tools. The server runs as a stdio subprocess spawned by each agent CLI invocation.

**Tool inventory** (8 tools, role-filtered):

| Tool | Roles | Replaces |
|------|-------|---------|
| `get_task` | all | `share/get_task.py` |
| `update_task_status` | all | `share/update_task.py` |
| `add_comment` | all | `share/add_comment_to_task.py` |
| `get_vision` | planner | `planner/get_vision.py` |
| `create_task` | planner | `planner/create_task.py` |
| `close_vision` | planner | `planner/close_vision.py` |
| `close_task` | coder | `coder/close_task.py` |
| `review_task` | qa | `qa/review_task.py` |

**Role filtering** is enforced server-side via `ORC_AGENT_ROLE` env var. A coder cannot call `create_task`; a planner cannot call `close_task`. This is a defense-in-depth measure on top of the CLI-level tool permissions below.

**Transport**: stdio. Each agent CLI spawns its own MCP server process, which inherits the correct working directory (the agent's git worktree) automatically via `Popen(cwd=worktree)`.

**Configuration** is generated per-agent at spawn time as a temp JSON file:

```json
{
  "mcpServers": {
    "orc": {
      "command": "python",
      "args": ["-m", "orc.mcp"],
      "env": {
        "ORC_API_SOCKET": "<socket-path>",
        "ORC_AGENT_ID": "<agent-id>",
        "ORC_AGENT_ROLE": "<role>"
      }
    }
  }
}
```

The temp file is cleaned up alongside the context file after the agent exits.

### 2. Add per-squad tool permission configuration

Tool permissions are declared in the squad YAML profile under a `permissions:` block. This is the natural place since squads already configure per-role `model` and `count`.

**Permission resolution order** (later entries win):

1. **Orc defaults** (always present in confined mode): `orc` (MCP), `read`, `write`, `shell(git:*)`
2. **Squad-level `permissions:`** — merged on top of defaults
3. **Per-role `permissions:`** — merged on top of squad-level

**Squad YAML format:**

```yaml
permissions:
  mode: confined       # "confined" (default) or "yolo"
  allow_tools:         # additional tools beyond orc defaults
    - "shell(just:*)"
  deny_tools:          # tools explicitly denied
    - "shell(git push:*)"

composition:
  - role: coder
    permissions:
      allow_tools:     # coder-specific extras
        - "shell(npm:*)"
```

**Yolo escape hatch** — `mode: yolo` at any level short-circuits to unrestricted (`--yolo` / `--dangerouslySkipPermissions`), matching the pre-MCP behaviour for teams that need it.

**Backend translation:**

| Abstract permission | Copilot CLI flag | Claude CLI flag |
|--------------------|-----------------|----------------|
| `orc` | `--allow-tool=orc` | `--allowedTools "mcp__orc__*"` |
| `read` | `--allow-tool=read` | `--allowedTools "Read"` |
| `write` | `--allow-tool=write` | `--allowedTools "Write"` |
| `shell(git:*)` | `--allow-tool='shell(git:*)'` | `--allowedTools "Bash(git *)"` |
| `shell(just:*)` | `--allow-tool='shell(just:*)'` | `--allowedTools "Bash(just *)"` |
| yolo | `--yolo` | `--dangerouslySkipPermissions` |

---

## Consequences

### Positive

- **Confined by default** — agents no longer need `--yolo`; tool access is controlled and auditable
- **Cleaner agent instructions** — tools are self-documenting via MCP schema; no more explaining script paths
- **No shell dependency** — board operations don't require a Python environment or shell access
- **Agent identity is automatic** — `ORC_AGENT_ID` is injected via env var; agents don't pass their own identity to tools
- **Role enforcement is layered** — CLI-level permission flags AND MCP server-side filtering

### Negative / Trade-offs

- **MCP SDK dependency** — adds the `mcp` Python package to orc's dependencies
- **Subprocess chain** — agent CLI spawns MCP server as a subprocess, adding one process layer
- **Per-agent config files** — a temp JSON file is written/cleaned per agent spawn (minor I/O overhead)

### Neutral

- **`agent_tools/` directory removed from templates** — existing projects initialized before this change will still have the `agent_tools/` directory, but agents will no longer reference it
- **Backward compat for ad-hoc invocations** — calls to `invoke()` / `spawn()` without a `permissions` argument default to yolo mode, preserving existing behaviour for conflict-resolution agents and other non-squad code paths
