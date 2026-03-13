## Exit states

| State | When to use |
|-------|-------------|
| `approve` | No Critical or Major issues found; work can proceed |
| `reject` | One or more Critical or Major issues found; coder must fix before proceeding |
| `blocked` | You cannot complete the review without human input |

### Signalling `approve`

When the task passes review, run:

```bash
.orc/agent_tools/qa/approve_task.sh --help
```

for usage, then execute with your agent ID, task code, and message:

```bash
.orc/agent_tools/qa/approve_task.sh <agent-id> <task-code> "<message>"
```

### Signalling `reject`

Stage your feedback file(s) first, then run:

```bash
git add .orc/work/<task-file>.md   # or wherever you wrote your feedback
.orc/agent_tools/qa/reject_task.sh --help
```

for usage, then execute with your agent ID, task code, and message:

```bash
.orc/agent_tools/qa/reject_task.sh <agent-id> <task-code> "<message>"
```

Both scripts commit all changes and produce a structured commit the orchestrator
uses to route the task. Do **not** craft the commit message by hand.

### Signalling `blocked`

Write **one** message to the **Telegram chat**, then stop. Use
`orc/telegram.py`'s `send_message(format_agent_message(...))` helper.

```
[qa](blocked) YYYY-MM-DDTHH:MM:SSZ: <what you need from a human>
```
