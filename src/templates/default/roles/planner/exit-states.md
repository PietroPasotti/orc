## Exit states

After completing your work, write **one** message to the **Telegram chat** using
the format below, then stop.  Use ``orc/telegram.py``'s
``send_message(format_agent_message(...))`` helper, or send the message
manually via your Telegram client.

| State | When to use |
|-------|-------------|
| `ready` | You created a new plan or ADR and the coder can now proceed |
| `blocked` | You cannot proceed without human input (explain what you need) |
| `done` | No more plans or ADRs to create; the vision is fully translated |

**Message format:**

```
[planner](state) YYYY-MM-DDTHH:MM:SSZ: <message>
```

Example:
```
[planner](ready) 2026-03-01T10:00:00Z: Created task 0003-add-resource-system.md. The coder should implement the ResourceType enum and wire it into the module.
```
