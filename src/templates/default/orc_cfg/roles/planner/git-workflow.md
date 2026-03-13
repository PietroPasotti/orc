## Git workflow

You work in the **dev worktree** (path given in the shared context under "Git workflow").

You are the **only agent that commits directly to `dev`**. After creating a task,
commit it using the provided tool:

```bash
.orc/agent_tools/planner/publish_task.py planner-1 .orc/work/NNNN-title.md
```

Do **not** craft the commit message by hand. All `git` commands must be run
from inside the dev worktree.

The board (board.yaml) and task files are stored in the project cache — the
agent tools handle their location automatically. You do **not** need to stage
board.yaml or task files for git.
