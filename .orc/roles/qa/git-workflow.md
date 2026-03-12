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
automatically once it detects your `qa(passed):` commit.

### Signalling your verdict via a git commit

After completing your review, you **must** make a commit on the feature branch.
This is how the orchestrator knows your verdict — it checks the last commit message
prefix on the branch.

**If passed** — no Critical or Major issues:
```bash
git commit --allow-empty -m "qa(passed): <one-line summary>"
```

**If failed** — one or more Critical or Major issues found:
1. Append an issues section to the task `.md` file in `.orc/work/` (see format below).
2. Then commit that change:
```bash
git add .orc/work/NNNN-task-title.md
git commit -m "qa(failed): <one-line summary of the blocking issue>"
```

The commit message must start with exactly `qa(passed):` or `qa(failed):` — the
orchestrator matches on this prefix.
