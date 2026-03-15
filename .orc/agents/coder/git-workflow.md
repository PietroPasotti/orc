## Git workflow

You work in your **feature worktree** — a dedicated directory for your branch. 

- Your branch is usually `feat/<task-stem>` (e.g. `feat/0003-resource-type-enum`), possibly with a prefix, cut
  from `main` or a dev branch. It already exists and the worktree is already checked out to it
  when you start.
- **All file edits and `git` commands must run inside your feature worktree.**
  Do not touch any other worktrees!.
- Commit all your work on your feature branch. Do **not** merge, rebase, or
  push to any other branch yourself.
- When you are done, leave the branch in its final committed state. The
  orchestrator will take it further after QA passes.

### Commit frequently

Each logical unit of work (a test + implementation pair, a refactor, a new
module) should be a separate commit. If the project uses a commit message convention, follow it. Always mention that this commit was your work (and add your agent ID to it).
