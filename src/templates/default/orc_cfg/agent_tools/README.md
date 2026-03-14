# Agent Tools

Python scripts that agents use to signal their exit state and manage tasks.
Using these scripts is **mandatory** — they ensure state is recorded correctly.

> ⚠️ **IMPORTANT — No direct `.orc/` filesystem access**
>
> **Never** read or write files under `.orc/work/`, `.orc/vision/`, or
> `board.yaml` directly. Always use the agent tool scripts below.

## Available tools

### Planner

```bash
# Fetch a vision file's content
.orc/agent_tools/planner/get_vision.py <vision-filename>
# Example:
.orc/agent_tools/planner/get_vision.py 0007-orc-status-board-view.md

# Create a new task (see Board Management section for full usage)
echo '<body-json>' | .orc/agent_tools/planner/create_task.py <agent-id> <task-title> <vision-file> [extra-file...]
# Example:
echo '{...}' | .orc/agent_tools/planner/create_task.py planner-1 add-user-auth 0001-auth-vision.md
# → prints the filename of the created task file

# Close a completed vision
.orc/agent_tools/planner/close_vision.py <vision-file> "<summary>" [task-name...]
```

### Coder

```bash
# Fetch a task file's content
.orc/agent_tools/coder/get_task.py <task-filename>
# Example:
.orc/agent_tools/coder/get_task.py 0003-add-user-auth.md

# Signal implementation done
.orc/agent_tools/coder/close_task.py <agent-id> <task-code> "<message>"
# Example:
.orc/agent_tools/coder/close_task.py coder-1 0002 "implemented auth module; all tests green"
```

### QA

```bash
# Signal review outcome (approved or rejected)
.orc/agent_tools/qa/review_task.py <agent-id> <task-code> approved|rejected "<message>"
# Examples:
.orc/agent_tools/qa/review_task.py qa-1 0002 approved "all tests green; no critical issues"
.orc/agent_tools/qa/review_task.py qa-2 0003 rejected "missing tests for error paths; see task file"
```

