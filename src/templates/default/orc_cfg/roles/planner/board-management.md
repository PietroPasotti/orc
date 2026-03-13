# Board management

Use the board management tool to create new tasks:

```bash
.orc/agent_tools/planner/create_task.py <task-title>
```

The tool reads the current counter from the board, creates the task file
from template, adds it to the `open` list with `status: planned`, and
increments the counter.

After running the tool, edit the created task file to fill in the overview,
scope, steps, and notes.  Then use `publish_task.py` to commit.

## Task status lifecycle

The board tracks each task's progress via a `status` field:

| Status | Meaning |
|---|---|
| `planned` | Planner created task, awaiting coder |
| `coding` | Coder actively working |
| `review` | Coder done, awaiting QA |
| `approved` | QA passed, ready to merge |
| `rejected` | QA failed, back to coder |
| `blocked` | Hard block, needs human help |
| `soft-blocked` | Soft block, planner can help |

Agents update this status using their provided tools — you do not set it manually.

## Task comments

Each task has a `comments` list for inter-agent and user-to-agent communication.
Check the comments on a task before creating follow-up tasks — they may contain
clarifications from previous agents or the user.
