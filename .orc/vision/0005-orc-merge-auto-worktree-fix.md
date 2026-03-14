# orc merge --auto: fix failure when dev worktree is active

## What

`orc merge --auto` currently crashes when the `dev` branch is checked out in a
worktree, because it tries to run `git checkout main` inside that worktree,
which git refuses.

Fix `orc merge --auto` so it performs the fast-forward merge correctly
regardless of whether dev is checked out as a worktree.

## Why

The `dev` worktree is always present while orc is running (it's how orc manages
integration). This means `orc merge --auto` is broken in the normal operating
mode — the user is always forced to do it manually.

## Constraints

- Perform the fast-forward by operating on the **main worktree** (i.e. the
  project root), not inside the dev worktree: `git -C <project-root> merge --ff-only dev`
- If the main worktree has an untracked file that would be overwritten by the
  merge, surface a clear error message explaining which file and why (e.g.
  "board.yaml exists as untracked in main worktree; remove it and re-run")
- Do not remove or touch user files silently

## Out of scope

- Handling merge conflicts (non-fast-forward situations)
- Removing the dev worktree after merge (separate concern)
