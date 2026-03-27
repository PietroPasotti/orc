# Git workflow

## Worktree

You operate in the **dev worktree** — this is a separate checkout of the
dev branch, isolated from the human's main worktree.

## Merge procedure

```bash
cd <dev-worktree>
git checkout <dev-branch>
git merge --no-ff <feature-branch> -m "Merge <feature-branch> into <dev-branch>"
```

If conflicts arise, resolve them manually, then:

```bash
git add <resolved-files>
git commit  # completes the merge
```

## After merge

The orchestrator will:
1. Remove the feature worktree.
2. Delete the feature branch.
3. Update the board (delete the task entry).

You do **not** need to perform these cleanup steps.
