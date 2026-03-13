## Exit states

| State | When to use |
|-------|-------------|
| `approve` | No Critical or Major issues found; work can proceed |
| `reject` | One or more Critical or Major issues found; coder must fix before proceeding |
| `blocked` | You cannot complete the review without human input |

### Signalling `approve`

When the task passes review, run:

```bash
.orc/agent_tools/qa/approve_task.sh <agent-id> <task-code> "<message>"
```

Example:
```bash
.orc/agent_tools/qa/approve_task.sh qa-1 0002 "all tests green; no critical issues"
```

### Signalling `reject`

Stage your feedback file(s) first, then run:

```bash
git add .orc/work/<task-file>.md   # or wherever you wrote your feedback
.orc/agent_tools/qa/reject_task.sh <agent-id> <task-code> "<message>"
```

Example:
```bash
git add .orc/work/0003-foo.md
.orc/agent_tools/qa/reject_task.sh qa-2 0003 "missing tests for error paths; see task file"
```

Both scripts commit all changes and produce a structured commit the orchestrator
uses to route the task. Do **not** craft the commit message by hand.

### Signalling `blocked`

Write **one** message to the **Telegram chat**, then stop. Use
`.orc/telegram.py`'s `send_message(format_agent_message(...))` helper.

```
[qa](blocked) YYYY-MM-DDTHH:MM:SSZ: <what you need from a human>
```
