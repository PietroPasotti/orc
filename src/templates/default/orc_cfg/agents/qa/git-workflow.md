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
automatically once you signal approval via `review_task.py`.

### Signalling your verdict

After completing your review, signal your verdict using the provided tool.

**If passed** — no Critical or Major issues:

```bash
.orc/agent_tools/qa/review_task.py <agent-id> <task-code> done "<one-line summary>"
```

**If failed** — one or more Critical or Major issues found:

1. Append an issues section to the task `.md` file (see format below).
2. Then run:

```bash
.orc/agent_tools/qa/review_task.py <agent-id> <task-code> in-progress "<one-line summary>"
```

Do **not** craft the commit message by hand.
