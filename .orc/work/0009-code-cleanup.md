# Task 0009 – Code cleanup: move chat.log to log dir, refactor merge helper, TUI feature count

## Overview

Three small `#TODO` comments identify independent cleanup and improvement
opportunities across different modules.  They are grouped here because each
is a contained, low-risk change that can be done in a single pass.

1. **`src/orc/config.py:69`** – `chat.log` is written to `orc_dir` (i.e.
   `.orc/`) instead of the configured `log_dir`.  It should follow the same
   log-directory configuration as `orc.log`.
2. **`src/orc/cli/merge.py:71`** – The `_merge()` function mixes CLI
   concerns (typer output, exit codes) with git logic that belongs in
   `src/orc/git/`.  Extract the git-level logic so it can be reused and
   tested independently.
3. **`src/orc/tui/run_tui.py:82`** – The TUI displays raw `dev_ahead` commit
   count.  This is less meaningful than a count of completed features
   (merged `feat/NNNN-*` branches).  Replace with a feature-completion count
   derived from the git log.

## Scope

**In scope:**
- `src/orc/config.py`: change the `chat_log` path to use `log_dir` instead
  of `orc_dir` (i.e. `log_dir / "chat.log"` instead of `orc_dir /
  "chat.log"`).  Update any downstream references.  Remove the `# TODO`
  comment (line 69).
- `src/orc/cli/merge.py`: extract the rebase + worktree-ensure logic from
  `_merge()` into a new helper in `src/orc/git/` (or generalise an existing
  one).  Keep `_merge()` as a thin CLI wrapper.  Remove the `# TODO` comment
  (line 71).
- `src/orc/tui/run_tui.py`: replace `dev_ahead: int` (raw commit count)
  with a `features_done: int` field that counts commits to `dev` whose
  message matches `Merge feat/NNNN-*`.  Update `render()` and its callers
  accordingly.  Remove the `# TODO` comment (line 82).
- Update affected tests for each change.

**Out of scope:**
- Changing the `orc.log` path or the log-rotation strategy.
- Implementing a full git log viewer or history panel in the TUI.
- Any changes to the merge algorithm itself (that is task 0005).

## Steps

- [ ] 1. **`src/orc/config.py`** – move `chat.log` into log dir:
  - Change the attribute or wherever `chat_log` path is computed from
    `orc_dir / "chat.log"` to `log_dir / "chat.log"`.
  - Grep for all consumers of the old path and update them.
  - Remove the `# TODO` comment.

- [ ] 2. **`src/orc/cli/merge.py`** – extract git logic:
  - Identify the git operations inside `_merge()` (rebase, worktree-ensure,
    etc.) and look for an existing function in `src/orc/git/` that overlaps.
  - Extract the git-heavy portion into a new function in `src/orc/git/`
    (e.g. `_prepare_merge()`), or generalise an existing one.
  - `_merge()` in `merge.py` becomes a thin wrapper: call the git helper,
    then do the typer output.
  - Remove the `# TODO` comment.

- [ ] 3. **`src/orc/tui/run_tui.py`** – show completed features:
  - Add a helper (or extend `src/orc/git/`) to count `Merge feat/NNNN-*`
    commits on `dev` that are not yet on `main`.
  - Replace the `dev_ahead` field in `RunState` (or whatever the TUI state
    dataclass is) with `features_done: int`.
  - Update `render()` to display e.g. `"3 features done"` instead of
    `"17 commits ahead"`.
  - Remove the `# TODO` comment.

- [ ] 4. Update affected tests:
  - `tests/test_config.py` (or equivalent) for the `chat.log` path change.
  - `tests/test_merge.py` for the extracted git helper.
  - `tests/test_tui.py` (or equivalent) for the renamed/replaced TUI field.

- [ ] 5. Run `just test` and `just lint`; fix any failures.

- [ ] 6. Commit:
  ```
  refactor: move chat.log to log dir, extract merge helper, TUI feature count
  ```

## Notes

- Sources:
  - `src/orc/config.py:69` (`# TODO: move chat.log into logs too`)
  - `src/orc/cli/merge.py:71` (`# TODO move this in git.py …`)
  - `src/orc/tui/run_tui.py:82` (`# TODO: instead of dev_ahead …`)
- Task 0005 also modifies `src/orc/cli/merge.py`.  Implement task 0009
  **after** task 0005 is merged to avoid conflicts.  The coder should rebase
  this branch on dev before starting.
- For step 3, the git query to count feature merges can use:
  ```bash
  git log dev --not main --merges --oneline --grep="^Merge feat/"
  ```
  and count the matching lines.
