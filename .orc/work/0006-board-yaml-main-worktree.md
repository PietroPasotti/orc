# 0006 – board.yaml: commit initial copy to `main` via `orc bootstrap`

## Overview

After `orc run`, a stale `board.yaml` accumulates in the main worktree at
`.orc/work/board.yaml` because the planner commits it to the `dev` branch but
`main` has never tracked it.  When `git merge --ff-only dev` is then attempted,
git aborts with "untracked working tree files would be overwritten".

The fix: `orc bootstrap` already creates `.orc/work/board.yaml` from the
template.  It should also stage and commit that file (along with the rest of
the scaffolded files) so `board.yaml` is tracked on `main` from day one.

Vision doc: `.orc/vision/0006-board-yaml-in-main-worktree.md`

## Scope

**In scope:**
- Extend `_bootstrap()` in `src/orc/cli/bootstrap.py`:
  - After scaffolding all files, if we are inside a git repository, run:
    ```bash
    git add .orc/work/board.yaml
    git commit -m "chore: bootstrap orc – add initial board.yaml"
    ```
    but **only if** `.orc/work/board.yaml` was newly created (i.e. it is in
    `created`, not `skipped`) and not already tracked by git.
  - If the git commands fail (e.g. no commits yet / bare repo), print a
    warning and continue — do not abort bootstrap.
  - If `.orc/work/board.yaml` is already tracked (the check via
    `git ls-files --error-unmatch`), skip the commit silently.
- Update `tests/test_bootstrap.py`:
  - `test_bootstrap_commits_board_yaml_to_main()`: mock subprocess; assert
    `git add` and `git commit` are called for board.yaml when it is newly
    created.
  - `test_bootstrap_skips_commit_when_already_tracked()`: assert no git commit
    when board.yaml is already tracked.
  - `test_bootstrap_commit_failure_is_non_fatal()`: assert bootstrap exits 0
    even if the git commit subprocess fails.

**Out of scope:**
- `orc upgrade` (the `--upgrade` path preserves `work/`; if board.yaml is
  already committed it will not be re-created).
- Changes to how the board is read or written.
- The merge error-handling improvement (that is task 0005).

## Steps

- [ ] 1. **`src/orc/cli/bootstrap.py`** – after the file-copy loop and before
  the summary output, add the git-commit step:

  ```python
  # Stage and commit board.yaml to main so it is tracked from day one.
  # This prevents "untracked working tree files would be overwritten" errors
  # when merging the dev branch back into main later.
  board_rel = Path(".orc") / "work" / "board.yaml"
  board_abs = project_root / board_rel
  if str(board_abs) in created:
      _try_commit_board(project_root, board_rel)
  ```

  Add a helper:
  ```python
  def _try_commit_board(project_root: Path, board_rel: Path) -> None:
      """Stage and commit board.yaml if it is not yet tracked by git."""
      # Check whether already tracked
      tracked = subprocess.run(
          ["git", "ls-files", "--error-unmatch", str(board_rel)],
          cwd=project_root,
          capture_output=True,
      )
      if tracked.returncode == 0:
          return  # already tracked, nothing to do

      stage = subprocess.run(
          ["git", "add", str(board_rel)],
          cwd=project_root,
          capture_output=True,
      )
      if stage.returncode != 0:
          typer.echo("⚠ Could not stage board.yaml (not a git repo?). Skipping commit.")
          return

      commit = subprocess.run(
          ["git", "commit", "-m", "chore: bootstrap orc – add initial board.yaml"],
          cwd=project_root,
          capture_output=True,
      )
      if commit.returncode != 0:
          # Rollback the staged file to leave the index clean
          subprocess.run(["git", "reset", "HEAD", str(board_rel)], cwd=project_root)
          typer.echo("⚠ Could not commit board.yaml. You may need to commit it manually.")
      else:
          typer.echo(f"✓ Committed initial {board_rel} to main.")
  ```

- [ ] 2. **`tests/test_bootstrap.py`** – add tests (see Scope above).

- [ ] 3. Run `just test` and `just lint`; fix any failures.

- [ ] 4. Commit:
  ```
  fix(bootstrap): commit initial board.yaml to main to prevent merge conflicts
  ```

## Notes

- The `_bootstrap()` function does not currently call any git commands; this
  is the first git interaction added there.  Keep it fully optional and
  non-fatal: if git is unavailable or the repo is in an unusual state,
  bootstrap must still succeed.
- `orc status` (`_dev_board_file()`) already prefers the dev-worktree copy
  of board.yaml when it exists, so having board.yaml tracked on both main and
  dev is correct — they will diverge as the planner commits tasks, and they
  reconcile cleanly on `git merge --ff-only dev`.
- This fix, combined with the error-handling improvement in task 0005, fully
  resolves the "untracked file" merge failure: bootstrap ensures main always
  tracks board.yaml, so the failure case in task 0005 only arises for users
  who bootstrapped with an older version of orc.
