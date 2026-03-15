## Git workflow

You work in the **dev worktree** (path given in the shared context under "Git workflow").

You are the **only agent that commits directly to `dev`**. After creating a task,
the `create_task.py` tool commits it automatically:

```bash
echo '{...}' | .orc/agent_tools/planner/create_task.py planner-1 NNNN-title 0001-vision.md
```

Do **not** craft the commit message by hand. All `git` commands must be run
from inside the dev worktree.

The board and task files are managed exclusively through the coordination API —
the agent tools handle all state access automatically. You do **not** need to stage
board or task files for git.
