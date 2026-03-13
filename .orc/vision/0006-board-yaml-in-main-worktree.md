# board.yaml: avoid stale copy in main worktree

## What

After `orc run`, a stale `board.yaml` is left in the main worktree (at
`.orc/work/board.yaml`) that does not match the committed version on `dev`.
This causes `git merge --ff-only dev` to abort with an "untracked file would be
overwritten" error.

Ensure the main worktree never accumulates a stale board.yaml that conflicts
with the dev branch.

## Why

The board lives on the `dev` branch (committed there by the planner). The main
worktree doesn't track it on `main` yet (it's untracked), so git refuses to
merge. This is a sharp edge that surprises users and breaks `orc merge`.

## Constraints

- One of:
  a. Add `.orc/work/board.yaml` to the project `.gitignore` so it is never
     treated as an untracked conflict, and orc reads it exclusively from the
     dev worktree path; or
  b. Commit an initial empty `board.yaml` to `main` during `orc bootstrap` so
     it is always a tracked file and git can overwrite it cleanly on merge
- Either approach must keep the board readable via `orc status` at all times

## Out of scope

- Changing the board format or location
- Multi-board support
