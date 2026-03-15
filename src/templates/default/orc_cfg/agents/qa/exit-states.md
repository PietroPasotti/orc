## Exit states

| State | When to use |
|-------|-------------|
| `done` | No Critical or Major issues found; work can proceed |
| `in-progress` | One or more Critical or Major issues found; coder must fix before proceeding |
| `blocked` | You cannot complete the review without human input |
| `stuck` | You cannot complete the review due to tooling, infrastructure, or permission constraints that no agent can resolve |

### Signalling `done` or `in-progress`

When the review is complete, call the `review_task` MCP tool:

`review_task(task_code="<code>", outcome="done|in-progress", message="<message>")`

- **done**: sets the board status to `done` and commits on the feature branch.
- **in-progress**: sets the board status to `in-progress`, adds a comment with the rejection
  reason, and commits. Do **not** craft the commit message by hand.

### Signalling `blocked`

Blocking requires **two steps** — updating the board and notifying the chat:

**Step 1:** Update the board status and leave a comment explaining the blocker.

Call the `update_task_status` MCP tool to set the task status:
```
update_task_status(task_code="<code>", status="blocked")
```

Call the `add_comment` MCP tool to add a comment describing what is blocking the review:
```
add_comment(task_code="<code>", comment="<reason>")
```

Example:
```
update_task_status(task_code="0003", status="blocked")
add_comment(task_code="0003", comment="blocked: cannot verify auth behaviour — staging environment is down")
```

**Step 2:** Write **one** message to the **Telegram chat**, then stop. Use
`.orc/telegram.py`'s `send_message(format_agent_message(...))` helper.

```
[qa](blocked) YYYY-MM-DDTHH:MM:SSZ: <what you need from a human>
```

### Signalling `stuck`

Use `stuck` when you cannot complete the review because a required tool or capability is unavailable due to MCP configuration, missing environment, or infra constraints — not missing spec (use `blocked` for that).

**Step 1:** Update the board and leave a detailed comment:

```
update_task_status(task_code="<code>", status="stuck")
add_comment(task_code="<code>", comment="stuck: <exact reason — what tool/resource is missing and why it is needed>")
```

**Step 2:** Stop. The orchestrator will notify the human automatically — you do not need to send a Telegram message.
