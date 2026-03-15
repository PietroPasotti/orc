## Exit states

| State | When to use |
|-------|-------------|
| `done` | You have implemented everything in the active plan and the CI is green |
| `soft-blocked` | The spec is ambiguous or conflicts with an ADR — needs planner clarification, not human input |
| `blocked` | You cannot proceed without human input |

### Signalling `done`

When your implementation is complete, call the `close_task` MCP tool:

- `task_code` — four-digit zero-padded task number, e.g. `"0002"`
- `message` — one-line summary of what was done (e.g. `"implemented auth module; all tests green"`)

The tool stages all changes, commits with a structured message, and sets the board status to `in-review`. Do **not** craft the commit message by hand.

### Signalling `soft-blocked` or `blocked`

Write **one** message to the **Telegram chat**, then stop. Use
`.orc/telegram.py`'s `send_message(format_agent_message(...))` helper.

```
[coder](soft-blocked) YYYY-MM-DDTHH:MM:SSZ: <what needs clarification>
[coder](blocked) YYYY-MM-DDTHH:MM:SSZ: <what you need from a human>
```
