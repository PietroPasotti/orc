## Exit states

| State | When to use |
|-------|-------------|
| `ready` | You created a new plan or ADR and the coder can now proceed |
| `blocked` | You cannot proceed without human input (explain what you need) |
| `done` | No more plans or ADRs to create; the vision is fully translated |

### Signalling `ready`

When you have created a task (or ADR), publish it with:

```bash
.orc/agent_tools/planner/publish_task.py <agent-id> <task-name> [extra-file...]
```

Arguments:
- `agent-id` — your agent identifier, e.g. `planner-1`
- `task-name` — task filename or name, e.g. `0003-add-foo` or `0003-add-foo.md`
- `extra-file` — optional extra files to stage (e.g. ADR docs you created)

Example:
```bash
.orc/agent_tools/planner/publish_task.py planner-1 0003-add-foo
.orc/agent_tools/planner/publish_task.py planner-1 0003-add-foo docs/adr/0042-foo.md
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
