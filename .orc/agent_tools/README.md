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
| `in-progress` | orchestrator | Coder actively working |
| `in-review` | `close_task.py` | Coder done, awaiting QA |
| `done` | `review_task.py done` | QA passed, ready to merge |
| `in-progress` | `review_task.py in-progress` | QA rejected, back to coder |
| `blocked` | — | Hard block, needs human help |

## Available tools

### Planner

```bash
# 1. Fetch a vision file's content from the server
.orc/agent_tools/planner/get_vision.py <vision-filename>
# Example:
.orc/agent_tools/planner/get_vision.py 0007-orc-status-board-view.md

# 2. Create a new task (calls API → writes to .orc/work/, updates board)
.orc/agent_tools/planner/create_task.py <task-title>
# Example:
.orc/agent_tools/planner/create_task.py add-user-auth
# → creates 0003-add-user-auth.md in .orc/work/, sets status: planned
# → prints the absolute path of the created file

# 3. Commit the task to dev (board lives in .orc/work/; optionally stage ADRs)
.orc/agent_tools/planner/publish_task.py <agent-id> <task-name> [extra-file...]
# Example:
.orc/agent_tools/planner/publish_task.py planner-1 0003-add-user-auth
.orc/agent_tools/planner/publish_task.py planner-1 0003-add-user-auth docs/adr/0042-auth.md

# 4. Close a completed vision (calls API → deletes from .orc/vision/, appends to changelog)
.orc/agent_tools/planner/close_vision.py <vision-file> "<summary>" [task-name...]
```

### Coder

```bash
# Fetch a task file's content from the server
.orc/agent_tools/coder/get_task.py <task-filename>
# Example:
.orc/agent_tools/coder/get_task.py 0003-add-user-auth.md

# Signal implementation done — sets board status to "in-review"
.orc/agent_tools/coder/close_task.py <agent-id> <task-code> "<message>"
# Example:
.orc/agent_tools/coder/close_task.py coder-1 0002 "implemented auth module; all tests green"
```

### QA

```bash
# Review a task — sets board status to "done" or "in-progress"
# (in-progress/rejected also posts the message as a comment for the coder)
.orc/agent_tools/qa/review_task.py <agent-id> <task-code> done|in-progress "<message>"
# Examples:
.orc/agent_tools/qa/review_task.py qa-1 0002 done "all tests green; no critical issues"
.orc/agent_tools/qa/review_task.py qa-2 0003 in-progress "missing tests for error paths; see task file"
```

## Task comments

Each board task has a `comments` list.  QA rejection feedback is written there
automatically by `review_task.py` when the outcome is `in-progress`.  You can also
add comments manually to communicate context to the next agent in the pipeline.
