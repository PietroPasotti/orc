## Exit states

After completing your work (or hitting a blocker), write **one** message to
the **Telegram chat** using the format below, then stop.  Use
``orc/telegram.py``'s ``send_message(format_agent_message(...))`` helper,
or send the message manually via your Telegram client.

| State | When to use                                                                             |
|-------|-----------------------------------------------------------------------------------------|
| `done` | You have implemented everything in the active plan and the CI is green                  |
| `soft-blocked` | The spec is ambiguous or conflicts with an ADR — needs planner clarification, not human input |
| `blocked` | You cannot proceed without human input                                                  |

**Message format:**

```
[coder](state) YYYY-MM-DDTHH:MM:SSZ: <message>
```

Example:
```
[coder](done) 2026-03-01T12:45:00Z: Implemented task 0002. All tests green.
```
