## Exit states

After completing your review, make the git commit described above, then write
**one** status message to the **Telegram chat** using the format below, then stop.
Use `orc/telegram.py`'s `send_message(format_agent_message(...))` helper.

| State | When to use |
|-------|-------------|
| `passed` | No Critical or Major issues found; work can proceed to the planner |
| `failed` | One or more Critical or Major issues found; coder must fix before proceeding |
| `blocked` | You cannot complete the review without human input |

**Message format:**

```
[qa](state) YYYY-MM-DDTHH:MM:SSZ: <message>
```

Example (passed):
```
[qa](passed) 2026-03-01T13:00:00Z: Reviewed plan 0002. No issues found.
```

Example (failed):
```
[qa](failed) 2026-03-01T13:05:00Z: Reviewed plan 0002. Found 1 critical issue:
[CRIT] Step 4 (server endpoint) is missing – GET /modules/{id}/inventory not implemented.
Coder must address [CRIT] before proceeding.
```

The Telegram message is informational only — the orchestrator routes based on
your git commit prefix, not this message.
