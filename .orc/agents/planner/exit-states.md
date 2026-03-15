## Exit states

| State | When to use |
|-------|-------------|
| `ready` | You created a new plan or ADR and the coder can now proceed |
| `blocked` | You cannot proceed without human input (explain what you need) |
| `done` | No more plans or ADRs to create; the vision is fully translated |

### Signalling `ready`

When you have a task ready, run `create_task.py` as described in the Board
Management section. Once the tool completes successfully, write **one** message
to the **Telegram chat**, then stop:

```
[planner](state) YYYY-MM-DDTHH:MM:SSZ: <message>
```

Example:
```
[planner](ready) 2026-03-01T10:00:00Z: Created task 0003-add-resource-system.md. The coder should implement the ResourceType enum and wire it into the module.
```
