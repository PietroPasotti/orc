## Exit states

| State | When to use |
|-------|-------------|
| `approve` | No Critical or Major issues found; work can proceed |
| `reject` | One or more Critical or Major issues found; coder must fix before proceeding |
| `blocked` | You cannot complete the review without human input |

### Signalling `approve` or `reject`

When the review is complete, run:

```bash
.orc/agent_tools/qa/review_task.py <agent-id> <task-code> approved|rejected "<message>"
```

- **approved**: sets the board status to `approved` and commits on the feature branch.
- **rejected**: sets the board status to `rejected`, adds a comment with the rejection
  reason, and commits.  Do **not** craft the commit message by hand.

### Signalling `blocked`

Write **one** message to the **Telegram chat**, then stop. Use
`.orc/telegram.py`'s `send_message(format_agent_message(...))` helper.

```
[qa](blocked) YYYY-MM-DDTHH:MM:SSZ: <what you need from a human>
```
