## Exit states

| State | When to use |
|-------|-------------|
| `done` | You have implemented everything in the active plan and the CI is green |
| `soft-blocked` | The spec is ambiguous or conflicts with an ADR — needs planner clarification, not human input |
| `blocked` | You cannot proceed without human input |

### Signalling `done`

When your implementation is complete, call the `close_task` MCP tool:

`close_task(task_code="<code>", message="<message>")`

Arguments:
- `task_code` — zero-padded 4-digit task number, e.g. `0002`
- `message` — one-line summary of what was done

Example:
```
close_task(task_code="0002", message="implemented auth module; all tests green")
```

This commits your changes and signals that implementation is complete. Do **not** craft the commit message by hand.

### Signalling `soft-blocked` or `blocked`

Both states require **two steps** — updating the board and notifying the chat:

**Step 1:** Update the board status and leave a comment explaining the blocker.

Call the `update_task_status` MCP tool to set the task status:
```
update_task_status(task_code="<code>", status="blocked")
```

Call the `add_comment` MCP tool to add a comment describing what is blocking you:
```
add_comment(task_code="<code>", comment="<reason>")
```

Example:
```
update_task_status(task_code="0002", status="blocked")
add_comment(task_code="0002", comment="blocked: API spec for /auth endpoint is missing — cannot implement without it")
```

**Step 2:** Write **one** message to the **Telegram chat**, then stop. Use
`.orc/telegram.py`'s `send_message(format_agent_message(...))` helper.

```
[coder](soft-blocked) YYYY-MM-DDTHH:MM:SSZ: <what needs clarification>
[coder](blocked) YYYY-MM-DDTHH:MM:SSZ: <what you need from a human>
```
