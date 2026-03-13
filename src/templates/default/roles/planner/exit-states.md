## Exit states

| State | When to use |
|-------|-------------|
| `ready` | You created a new plan or ADR and the coder can now proceed |
| `blocked` | You cannot proceed without human input (explain what you need) |
| `done` | No more plans or ADRs to create; the vision is fully translated |

### Signalling `ready`

When you have created a task (or ADR), publish it with:

```bash
.orc/agent_tools/planner/publish_task.sh --help
```

for usage, then execute with your agent ID, task file, and any extra files:

```bash
.orc/agent_tools/planner/publish_task.sh <agent-id> <task-file> [extra-files...]
```

This produces `chore(planner-1.ready.0003): add task 0003-add-foo` on the `dev`
branch and signals that the coder can proceed.

Then write **one** message to the **Telegram chat**, then stop:

```
[planner](state) YYYY-MM-DDTHH:MM:SSZ: <message>
```

Example:
```
[planner](ready) 2026-03-01T10:00:00Z: Created task 0003-add-resource-system.md. The coder should implement the ResourceType enum and wire it into the module.
```
