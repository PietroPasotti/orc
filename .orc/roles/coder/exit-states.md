## Exit states

| State | When to use |
|-------|-------------|
| `done` | You have implemented everything in the active plan and the CI is green |
| `soft-blocked` | The spec is ambiguous or conflicts with an ADR — needs planner clarification, not human input |
| `blocked` | You cannot proceed without human input |
| `stuck` | You cannot proceed due to tooling, infrastructure, or permission constraints that no agent can resolve |

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

### Signalling `stuck`

Use `stuck` when you need a tool or capability that is unavailable due to MCP configuration, missing environment variables, infra outages, or other constraints that only a human operator can resolve — not spec ambiguity (use `blocked` for that).

**Step 1:** Update the board and leave a detailed comment:

```
update_task_status(task_code="<code>", status="stuck")
add_comment(task_code="<code>", comment="stuck: <exact reason — what tool/resource is missing and why it is needed>")
```

**Step 2:** Stop. The orchestrator will notify the human automatically — you do not need to send a Telegram message.
