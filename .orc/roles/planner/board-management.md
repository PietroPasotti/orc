# Board management

Use the `create_task` MCP tool to create new tasks:

- `task_title` — dash-separated title, e.g. `"add-user-auth"`
- `vision_file` — source vision filename
- `overview`, `in_scope`, `out_of_scope`, `steps`, `notes` — task content
- `extra_files` — optional extra files to commit alongside (e.g. ADR docs)

The tool reads the current counter from the board, creates the task file,
adds it to the `tasks` list with `status: planned`, increments the counter,
and commits everything in one step.

## Task status lifecycle

The board tracks each task's progress via a `status` field:

| Status | Meaning |
|---|---|
| `planned` | Planner created task, awaiting coder |
| `in-progress` | Coder actively working |
| `in-review` | Coder done, awaiting QA |
| `done` | QA passed, ready to merge |
| `blocked` | Hard block, needs human help |

Agents update this status using their provided tools — you do not set it manually.

## Task comments

Each task has a `comments` list for inter-agent and user-to-agent communication.
Check the comments on a task before creating follow-up tasks — they may contain
clarifications from previous agents or the user.
