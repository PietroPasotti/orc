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
# Create a new task and commit it to dev in one step.
# The structured body (JSON) is read from stdin; the server assembles the markdown.
echo '{
  "overview":     "<what and why>",
  "in_scope":     ["item 1", "item 2"],
  "out_of_scope": ["item 1"],
  "steps":        ["step 1", "step 2"],
  "notes":        "<optional>"
}' | .orc/agent_tools/planner/create_task.py <agent-id> <task-title> <vision-file> [extra-file...]

# Example:
echo '{
  "overview": "Add JWT-based authentication to the API.",
  "in_scope": ["login endpoint", "token refresh"],
  "out_of_scope": ["OAuth integration", "UI changes"],
  "steps": ["Write failing tests", "Implement auth middleware", "Wire into routes"],
  "notes": "See ADR-0042 for the chosen algorithm."
}' | .orc/agent_tools/planner/create_task.py planner-1 add-user-auth 0001-auth-vision.md
# → creates 0003-add-user-auth.md in .orc/work/, sets status: planned, commits to dev
# → prints the filename of the created task file

# With an optional extra file (e.g. a new ADR):
echo '{...}' | .orc/agent_tools/planner/create_task.py planner-1 add-user-auth 0001-auth-vision.md docs/adr/0042-auth.md

# Close a completed vision (calls API → deletes from .orc/vision/, appends to changelog)
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
