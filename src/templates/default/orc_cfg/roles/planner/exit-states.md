## Exit states

| State | When to use |
|-------|-------------|
| `ready` | You created a new plan or ADR and the coder can now proceed |
| `blocked` | You cannot proceed without human input (explain what you need) |
| `done` | No more plans or ADRs to create; the vision is fully translated |

### Signalling `ready`

When you have a task ready, create and commit it in one step:

```bash
echo '<body-json>' | .orc/agent_tools/planner/create_task.py <agent-id> <task-title> <vision-file> [extra-file...]
```

Arguments:
- `agent-id` — your agent identifier, e.g. `planner-1`
- `task-title` — short dash-separated title, e.g. `add-user-auth`
- `vision-file` — filename of the vision this task was refined from, e.g. `0001-auth-vision.md`
- `extra-file` — optional extra files to stage (e.g. ADR docs you created)

Body (JSON on stdin):
```json
{
  "overview":      "<what is being built and why>",
  "in_scope":      ["item 1", "item 2"],
  "out_of_scope":  ["item 1"],
  "steps":         ["step 1", "step 2"],
  "notes":         "<blockers, design decisions, tips for the coder>"
}
```

Example:
```bash
echo '{
  "overview": "Add JWT-based authentication to the API.",
  "in_scope": ["login endpoint", "token refresh"],
  "out_of_scope": ["OAuth integration", "UI changes"],
  "steps": ["Write failing tests", "Implement auth middleware", "Wire into routes"],
  "notes": "See ADR-0042 for the chosen algorithm."
}' | .orc/agent_tools/planner/create_task.py planner-1 add-user-auth 0001-auth-vision.md
```

With an optional extra file:
```bash
echo '{...}' | .orc/agent_tools/planner/create_task.py planner-1 add-user-auth 0001-auth-vision.md docs/adr/0042-auth.md
```

This commits to the `dev` branch and signals that the coder can proceed.

Then write **one** message to the **Telegram chat**, then stop:

```
[planner](state) YYYY-MM-DDTHH:MM:SSZ: <message>
```

Example:
```
[planner](ready) 2026-03-01T10:00:00Z: Created task 0003-add-resource-system.md. The coder should implement the ResourceType enum and wire it into the module.
```
