# Agent Tools

Shell scripts that agents use to signal their exit state via a structured git
commit.  Using these scripts is mandatory — hand-crafting exit commits is
error-prone and wastes tokens.

## Commit format

Every exit commit follows this conventional-commit structure:

```
chore(<agent-id>.<action>.<task-code>): <message>
```

| Field       | Format          | Example             |
|-------------|-----------------|---------------------|
| `agent-id`  | `{role}-{n}`    | `coder-1`, `qa-2`   |
| `action`    | see table below | `done`, `approve`   |
| `task-code` | `NNNN` (4 digits) | `0002`, `0005`    |

The orchestrator parses this scope to determine which agent to dispatch next.

## Available tools

### Coder

| Script | Action | Routing effect |
|--------|--------|----------------|
| `coder/close_task.sh` | `done` | → QA review |

```bash
.orc/agent_tools/coder/close_task.sh <agent-id> <task-code> "<message>"

# Example:
.orc/agent_tools/coder/close_task.sh coder-1 0002 "implemented auth module; all tests green"
# Produces: chore(coder-1.done.0002): implemented auth module; all tests green
```

### QA

| Script | Action | Routing effect |
|--------|--------|----------------|
| `qa/approve_task.sh` | `approve` | → merge into dev |
| `qa/reject_task.sh`  | `reject`  | → back to coder  |

```bash
.orc/agent_tools/qa/approve_task.sh <agent-id> <task-code> "<message>"

# Example:
.orc/agent_tools/qa/approve_task.sh qa-1 0002 "all tests green; no critical issues"
# Produces: chore(qa-1.approve.0002): all tests green; no critical issues
```

```bash
# Stage feedback files first, then reject:
git add orc/work/0003-foo.md
.orc/agent_tools/qa/reject_task.sh <agent-id> <task-code> "<message>"

# Example:
.orc/agent_tools/qa/reject_task.sh qa-2 0003 "missing tests for error paths; see task file"
# Produces: chore(qa-2.reject.0003): missing tests for error paths; see task file
```

### Planner

| Script | Action | Effect |
|--------|--------|--------|
| `planner/publish_task.sh` | `ready` | commits task file + board to dev |

```bash
.orc/agent_tools/planner/publish_task.sh <agent-id> <task-file> [extra-files...]

# Example:
.orc/agent_tools/planner/publish_task.sh planner-1 .orc/work/0003-add-foo.md .orc/work/board.yaml
# Produces: chore(planner-1.ready.0003): add task 0003-add-foo
```

## Arguments

### Coder and QA scripts

All accept the same three positional arguments:

| # | Argument     | Description                          |
|---|--------------|--------------------------------------|
| 1 | `agent-id`   | Your agent ID, e.g. `coder-1`, `qa-2` |
| 2 | `task-code`  | 4-digit task number, e.g. `0002`     |
| 3 | `message`    | One-line summary (quoted)            |

### Planner script

| # | Argument     | Description                                  |
|---|--------------|----------------------------------------------|
| 1 | `agent-id`   | Your agent ID, e.g. `planner-1`             |
| 2 | `task-file`  | Path to the new task file (first staged file)|
| 3+ | `extra-files` | Additional files to stage (e.g. board.yaml) |

The task code and commit message are derived automatically from the task filename.
