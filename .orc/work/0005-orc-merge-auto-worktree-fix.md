# 0005 – `orc merge --auto`: graceful error when main worktree has untracked files

## Overview

`orc merge --auto` fails with an unhandled `CalledProcessError` when the main
worktree contains an untracked file (e.g. `board.yaml`) that would be
overwritten by the fast-forward merge.  Instead, orc should catch this
situation and print a clear, actionable error message.

Vision doc: `.orc/vision/0005-orc-merge-auto-worktree-fix.md`

## Scope

**In scope:**
- Modify `_complete_merge()` in `src/orc/git/core.py`:
  - Remove `check=True`; capture stderr; check returncode manually.
  - When `returncode != 0` and stderr contains
    `"untracked working tree files would be overwritten"`, parse the file list
    from the git output and raise a descriptive exception (or return a
    structured error value) naming the conflicting files.
  - For any other non-zero return, re-raise / raise a `RuntimeError` with the
    raw stderr so the caller still gets an informative message.
- Modify `_merge()` in `src/orc/cli/merge.py`:
  - Catch the untracked-file error from `_complete_merge()`.
  - Print a human-readable message: e.g.
    `"✗ board.yaml exists as untracked in the main worktree.  Remove it and re-run orc merge --auto."`
  - Exit with a non-zero code (`raise typer.Exit(code=1)`).
- Update `tests/test_merge.py`:
  - `test_auto_merge_fails_with_untracked_file_error()`: mock
    `_git._complete_merge()` to simulate the git untracked-file failure;
    assert the CLI prints the file name and exits non-zero.
  - `test_auto_merge_generic_failure_re_raises()`: mock `_complete_merge()` to
    simulate a generic non-zero exit; assert it propagates as a non-zero exit.

**Out of scope:**
- Automatically removing or staging the conflicting files.
- Fixing the root cause of the stale `board.yaml` (that is task 0006).
- Handling non-fast-forward merge conflicts.

## Steps

- [ ] 1. **`src/orc/git/core.py`** – update `_complete_merge()`:
  ```python
  def _complete_merge() -> bool:
      cfg = _cfg.get()
      result = subprocess.run(
          ["git", "merge", "--ff-only", cfg.work_dev_branch],
          cwd=cfg.repo_root,
          capture_output=True,
          text=True,
      )
      if result.returncode == 0:
          return "Already up to date" not in result.stdout
      stderr = result.stderr + result.stdout
      if "untracked working tree files would be overwritten" in stderr:
          # Extract the listed filenames from git's output
          files = _parse_untracked_conflict_files(stderr)
          raise UntrackedConflictError(files)
      raise RuntimeError(f"git merge --ff-only failed:\n{stderr}")
  ```
  Add `UntrackedConflictError(Exception)` (or a similar named exception) to
  `src/orc/git/core.py` (or a new `src/orc/git/errors.py` — whichever fits
  best).  The exception should carry the list of conflicting file paths as an
  attribute.

  Add `_parse_untracked_conflict_files(stderr: str) -> list[str]` to parse
  the file list from git's standard error output (lines between the
  "untracked" header and the "Please move or remove" line).

- [ ] 2. **`src/orc/cli/merge.py`** – update `_merge()`:
  ```python
  from orc.git.core import UntrackedConflictError  # (adjust import path)

  def _merge(auto: bool = False) -> None:
      ...
      if auto:
          try:
              merged = _git._complete_merge()
          except UntrackedConflictError as exc:
              for f in exc.files:
                  typer.echo(
                      f"✗ {f} exists as untracked in the main worktree. "
                      "Remove it and re-run `orc merge --auto`."
                  )
              raise typer.Exit(code=1)
          if merged:
              typer.echo("✓ dev merged into main.")
          else:
              typer.echo("Already up to date.")
  ```

- [ ] 3. **`tests/test_merge.py`** – add tests (see Scope above).

- [ ] 4. Run `just test` and `just lint`; fix any failures.

- [ ] 5. Commit:
  ```
  fix(merge): surface clear error when main worktree has untracked conflict files
  ```

## Notes

- `_complete_merge()` already uses `cwd=cfg.repo_root` (the main worktree),
  so no `git checkout` is needed — the existing code structure is correct.
  Only the error handling needs improvement.
- The git error message format for untracked conflicts looks like:
  ```
  error: The following untracked working tree files would be overwritten by merge:
          .orc/work/board.yaml
  Please move or remove them before you merge.
  ```
  Parse the indented file lines between these two markers.
