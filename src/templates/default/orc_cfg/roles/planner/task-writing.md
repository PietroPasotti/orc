## Write tasks that stand alone

A task file is both a task list and a state document. If the coder runs out of
context mid-way, the next coder agent must be able to resume from the task file alone.

Each task is created by passing a structured JSON body to `create_task.py` (stdin).
The server assembles these fields into the markdown file:

| Field | Type | Description |
|---|---|---|
| `overview` | string | What is being built and why, with references to relevant ADRs |
| `in_scope` | list of strings | What this task covers |
| `out_of_scope` | list of strings | What is explicitly excluded |
| `steps` | list of strings | Ordered implementation steps (rendered as `- [ ] N. step`) |
| `notes` | string | Blockers, design decisions, and tips for the coder |

All fields should be complete before calling `create_task.py` — there is no separate editing step.
