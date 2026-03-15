## Exit states

| State | When to use |
|-------|-------------|
| `done` | No Critical or Major issues found; work can proceed |
| `in-progress` | One or more Critical or Major issues found; coder must fix before proceeding |
| `blocked` | You cannot complete the review without human input |

### Signalling `done` or `in-progress`

When the review is complete, run:

```bash
.orc/agent_tools/qa/review_task.py <agent-id> <task-code> done|in-progress "<message>"
```

- **done**: sets the board status to `done` and commits on the feature branch.
- **in-progress**: sets the board status to `in-progress`, adds a comment with the rejection
  reason, and commits.  Do **not** craft the commit message by hand.

### Signalling `blocked`

Blocking requires **two steps** — updating the board and notifying the chat:

**Step 1:** Update the board status and leave a comment explaining the blocker:

```bash
# Set the task status to blocked
.orc/agent_tools/share/update_task.py <task-code> blocked

# Add a comment describing what is blocking the review
.orc/agent_tools/share/add_comment_to_task.py <agent-id> <task-code> "<reason>"
```

Example:
```bash
.orc/agent_tools/share/update_task.py 0003 blocked
.orc/agent_tools/share/add_comment_to_task.py qa-1 0003 "blocked: cannot verify auth behaviour — staging environment is down"
```

**Step 2:** Write **one** message to the **Telegram chat**, then stop. Use
`.orc/telegram.py`'s `send_message(format_agent_message(...))` helper.

```
[qa](blocked) YYYY-MM-DDTHH:MM:SSZ: <what you need from a human>
```
