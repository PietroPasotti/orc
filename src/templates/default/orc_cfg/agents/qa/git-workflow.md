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

After completing your review, signal your verdict using the `review_task` MCP tool.

**If passed** — no Critical or Major issues:

Call: `review_task(task_code="<code>", outcome="done", message="<one-line summary>")`

**If failed** — one or more Critical or Major issues found:

1. Append an issues section to the task `.md` file (see format below).
2. Then call: `review_task(task_code="<code>", outcome="in-progress", message="<one-line summary>")`

Do **not** craft the commit message by hand.
