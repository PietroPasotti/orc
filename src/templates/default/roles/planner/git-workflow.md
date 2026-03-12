## Git workflow

You work in the **dev worktree** (path given in the shared context under "Git workflow").

You are the **only agent that commits directly to `dev`**. Task files and ADRs
you create must be committed to the `dev` branch:

```
git add orc/work/NNNN-title.md orc/work/board.yaml
git commit -m "chore(orc): add task NNNN-title"
```

All `git` commands must be run from inside the dev worktree.
