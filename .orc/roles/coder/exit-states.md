## Exit states

| State | When to use |
|-------|-------------|
| `done` | You have implemented everything in the active plan and the CI is green |
| `soft-blocked` | The spec is ambiguous or conflicts with an ADR — needs planner clarification, not human input |
| `blocked` | You cannot proceed without human input |

### Signalling `done`

When your implementation is complete, run:

```bash
.orc/agent_tools/coder/close_task.sh <agent-id> <task-code> "<message>"
```

Example:
```bash
.orc/agent_tools/coder/close_task.sh coder-1 0002 "implemented auth module; all tests green"
```

This commits all your changes and produces a structured commit the orchestrator
uses to route the task to QA. Do **not** craft the commit message by hand.

### Signalling `soft-blocked` or `blocked`

Write **one** message to the **Telegram chat**, then stop. Use
`.orc/telegram.py`'s `send_message(format_agent_message(...))` helper.

```
[coder](soft-blocked) YYYY-MM-DDTHH:MM:SSZ: <what needs clarification>
[coder](blocked) YYYY-MM-DDTHH:MM:SSZ: <what you need from a human>
```
