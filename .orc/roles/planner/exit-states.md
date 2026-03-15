## Exit states

| State | When to use |
|-------|-------------|
| `ready` | You created a new plan or ADR and the coder can now proceed |
| `blocked` | You cannot proceed without human input (explain what you need) |
| `done` | No more plans or ADRs to create; the vision is fully translated |

### Signalling `ready`

When you have created a task (or ADR), call the `create_task` MCP tool:

- `task_title` — dash-separated title, e.g. `"add-user-auth"`
- `vision_file` — source vision filename, e.g. `"0001-auth-vision.md"`
- `overview`, `in_scope`, `out_of_scope`, `steps`, `notes` — task content
- `extra_files` — optional list of file paths to commit alongside (e.g. ADR docs)

The tool creates the task file, adds it to the board, commits, and signals that the coder can proceed.

Then write **one** message to the **Telegram chat**, then stop:

```
[planner](state) YYYY-MM-DDTHH:MM:SSZ: <message>
```

Example:
```
[planner](ready) 2026-03-01T10:00:00Z: Created task 0003-add-resource-system.md. The coder should implement the ResourceType enum and wire it into the module.
```
