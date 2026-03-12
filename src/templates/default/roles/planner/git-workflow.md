## Git workflow

You work in the **dev worktree** (path given in the shared context under "Git workflow").

You are the **only agent that commits directly to `dev`**. Task files and ADRs
you create must be committed to the `dev` branch using the provided tool:

```bash
.orc/agent_tools/planner/publish_task.sh orc/work/NNNN-title.md orc/work/board.yaml
```

Do **not** craft the commit message by hand. All `git` commands must be run
from inside the dev worktree.
