## Git workflow

You work in the **feature worktree** (path given in the shared context under "Git workflow").
The feature worktree is already checked out on the feature branch (`feat/NNNN-task-title`),
so you can inspect the changes directly:

```bash
# Review commits on the feature branch vs main:
git log main..HEAD --oneline

# Inspect a specific commit:
git show <sha>

# Full diff of the feature branch:
git diff main..HEAD
```

**Do NOT merge the feature branch yourself.** The orchestrator handles the merge
automatically once you signal approval via the `review_task` MCP tool.

### Signalling your verdict

After completing your review, call the `review_task` MCP tool to signal your verdict.
It updates the board status and makes a commit — the orchestrator reads
the board to route the task.

- `task_code` — four-digit zero-padded task number, e.g. `"0002"`
- `outcome` — `"done"` (approved) or `"in-progress"` (rejected, sends back to coder)
- `message` — one-line summary (reason for rejection if applicable)

**If passed** — no Critical or Major issues: call `review_task` with `outcome="done"`.

**If failed** — one or more Critical or Major issues found:

1. Append an issues section to the task `.md` file (see format below).
2. Call `review_task` with `outcome="in-progress"`.

Do **not** craft the commit message by hand.
