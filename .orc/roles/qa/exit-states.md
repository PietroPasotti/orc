## Exit states

| State | When to use |
|-------|-------------|
| `done` | No Critical or Major issues found; work can proceed |
| `in-progress` | One or more Critical or Major issues found; coder must fix before proceeding |
| `blocked` | You cannot complete the review without human input |
| `stuck` | You cannot complete the review due to tooling, infrastructure, or permission constraints that no agent can resolve |

### Signalling `done` or `in-progress`

When the review is complete, call the `review_task` MCP tool:

- `task_code` — four-digit zero-padded task number, e.g. `"0002"`
- `outcome` — `"done"` to approve or `"in-progress"` to reject
- `message` — summary of the review outcome (reason for rejection if applicable)

The tool commits any staged changes, updates the board status, and (on rejection) appends a comment with the rejection reason.  Do **not** craft the commit message by hand.

### Signalling `blocked`

Write **one** message to the **Telegram chat**, then stop. Use
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
