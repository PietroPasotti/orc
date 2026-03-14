# Agent Tools

Python scripts that agents use to update the board and signal their exit state.
Using these scripts is **mandatory** — hand-crafting board updates is error-prone
and wastes tokens.

> ⚠️ **IMPORTANT — No direct `.orc/` filesystem access**
>
> All board and vision state is managed exclusively through the **orc coordination
> API** (a Unix socket server started by `orc run`).  **Never** read or write files
> under `.orc/work/`, `.orc/vision/`, or `board.yaml` directly.  The project may
> use git worktrees where each worktree has its own `.orc/` copy; direct access will
> silently write to the wrong directory.  Always use the agent tool scripts below —
> they talk to the coordination API via the `ORC_API_SOCKET` environment variable
> set by the orchestrator.

## How signalling works

Agents communicate their status by calling the **coordination API**, which updates
the **board** (stored in `.orc/work/`, gitignored).  The orchestrator polls the
board and dispatches the next agent based on each task's `status` field.  There is
no commit-message parsing — the board is the single source of truth.

| Status | Set by | Meaning |
|---|---|---|
| `planned` | `create_task.py` | Planner created task, awaiting coder |
| `coding` | orchestrator | Coder actively working |
| `review` | `close_task.py` | Coder done, awaiting QA |
| `approved` | `approve_task.py` | QA passed, ready to merge |
| `rejected` | `reject_task.py` | QA failed, back to coder |
| `blocked` | — | Hard block, needs human help |

## Available tools

### Planner

```bash
# 1. Create a new task (calls API → writes to .orc/work/, updates board)
.orc/agent_tools/planner/create_task.py <task-title>
# Example:
.orc/agent_tools/planner/create_task.py add-user-auth
# → creates 0003-add-user-auth.md in .orc/work/, sets status: planned
# → prints the absolute path of the created file

# 2. Commit the task to dev (board lives in .orc/work/; optionally stage ADRs)
.orc/agent_tools/planner/publish_task.py <agent-id> <task-name> [extra-file...]
# Example:
.orc/agent_tools/planner/publish_task.py planner-1 0003-add-user-auth
.orc/agent_tools/planner/publish_task.py planner-1 0003-add-user-auth docs/adr/0042-auth.md

# 3. Close a completed vision (calls API → deletes from .orc/vision/, appends to changelog)
.orc/agent_tools/planner/close_vision.py <vision-file> "<summary>" [task-name...]
```

### Coder

```bash
# Signal implementation done — sets board status to "review"
.orc/agent_tools/coder/close_task.py <agent-id> <task-code> "<message>"
# Example:
.orc/agent_tools/coder/close_task.py coder-1 0002 "implemented auth module; all tests green"
```

### QA

```bash
# Approve — sets board status to "approved"
.orc/agent_tools/qa/approve_task.py <agent-id> <task-code> "<message>"
# Example:
.orc/agent_tools/qa/approve_task.py qa-1 0002 "all tests green; no critical issues"

# Reject — sets board status to "rejected", adds comment with feedback
.orc/agent_tools/qa/reject_task.py <agent-id> <task-code> "<message>"
# Example:
.orc/agent_tools/qa/reject_task.py qa-2 0003 "missing tests for error paths; see task file"
```

## Task comments

Each board task has a `comments` list.  QA rejection feedback is written there
automatically by `reject_task.py`.  You can also add comments manually to
communicate context to the next agent in the pipeline.
