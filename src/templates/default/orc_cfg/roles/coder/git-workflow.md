## Git workflow

You work in your **feature worktree** — a dedicated directory for your branch,
separate from the dev and main worktrees. The path and branch name are given
in the shared context under "Git workflow".

- Your branch is `feat/<task-stem>` (e.g. `feat/0003-resource-type-enum`), cut
  from `main`. It already exists and the worktree is already checked out to it
  when you start.
- **All file edits and `git` commands must run inside your feature worktree.**
  Do not touch the dev or main worktrees.
- Commit all your work on your feature branch. Do **not** merge, rebase, or
  push to `dev` or `main` yourself.
- When you are done, leave the branch in its final committed state. The
  orchestrator will merge it into `dev` after QA passes.

### Commit frequently

Each logical unit of work (a test + implementation pair, a refactor, a new
module) should be a separate commit. Use conventional commit format:

```
feat(model): add ResourceType enum
test(model): cover ResourceType serialisation
```

Run `just lint` before committing to avoid hook failures.
