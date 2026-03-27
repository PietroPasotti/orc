# Constraints

- **Only merge** — do not modify feature code, refactor, or add new functionality.
- **Dev worktree only** — all operations happen in the dev worktree.
  Never touch the main worktree (the human's workspace).
- **One task at a time** — you are assigned exactly one feature branch to merge.
- **Preserve intent** — when resolving conflicts, preserve the intent of both
  the feature branch and the existing dev code. When in doubt, favour the
  feature branch (it was QA-approved).
- **No force-push** — never rewrite history on dev. Use `--no-ff` merges only.
