# Task 0010 – Code cleanup: move chat.log, extract merge helper, TUI feature count

## Overview

Three `#TODO` comments in the codebase identify small but meaningful improvements.
They were grouped into task 0009 but that task was closed via recovery without
the implementation being delivered.  This task re-tracks the same work.

1. **`src/orc/config.py:69`** – `chat.log` is written to `orc_dir` (`.orc/`)
   instead of the configured `log_dir`.  It should live alongside `orc.log`.
2. **`src/orc/cli/merge.py:72`** – A git helper function sits in the CLI module
   instead of `src/orc/git/` where it belongs.
3. **`src/orc/tui/run_tui.py:82`** – The TUI shows raw `dev_ahead` commit count;
   a count of completed features (merged `feat/NNNN-*` branches) is more useful.

## Scope

**In scope:**
- `src/orc/config.py`: change `chat_log` path from `orc_dir / "chat.log"` to
  `log_dir / "chat.log"`.  Update all downstream consumers.  Remove the `# TODO`
  comment.
- `src/orc/cli/merge.py`: move the git-level helper identified by the `# TODO`
  into `src/orc/git/` (reuse or generalise an existing function if possible).
  Keep `_merge()` as a thin CLI wrapper.  Remove the `# TODO` comment.
- `src/orc/tui/run_tui.py`: replace `dev_ahead: int` (raw commit count) with
  `features_done: int` that counts `Merge feat/NNNN-*` commits on `dev` not yet
  on `main`.  Update `render()` and callers.  Remove the `# TODO` comment.
- Update all affected tests.

**Out of scope:**
- Changing the `orc.log` path or log-rotation strategy.
- Implementing a full git-history viewer in the TUI.
- Any changes to the merge algorithm itself.

## Steps

- [ ] 1. **`src/orc/config.py`** – move `chat.log` into log dir:
  - Change `orc_dir / "chat.log"` → `log_dir / "chat.log"` wherever the path
    is computed (check `config.py` and `src/orc/messaging/telegram.py`).
  - Remove the `# TODO: move chat.log into logs too` comment.

- [ ] 2. **`src/orc/cli/merge.py`** – extract git logic:
  - Move the function flagged by `# TODO move this in git.py` into
    `src/orc/git/` (or generalise an existing helper there).
  - `_merge()` in `merge.py` becomes a thin wrapper.
  - Remove the `# TODO` comment.

- [ ] 3. **`src/orc/tui/run_tui.py`** – show completed features:
  - Add a helper (or extend `src/orc/git/`) to count `Merge feat/NNNN-*`
    commits on `dev` not yet on `main`.
  - Rename `dev_ahead` → `features_done` in `RunState` (or equivalent).
  - Update `render()` to display e.g. `"3 features done"` rather than
    `"17 commits ahead"`.
  - Remove the `# TODO` comment.

- [ ] 4. Update affected tests (config, merge, TUI).

- [ ] 5. Run `just test` and `just lint`; fix any failures.

- [ ] 6. Commit:
  ```
  refactor: move chat.log to log dir, extract merge helper, TUI feature count
  ```

## Notes

- Source references: `src/orc/config.py:69`, `src/orc/cli/merge.py:72`,
  `src/orc/tui/run_tui.py:82`.
- The `chat.log` consumer lives in `src/orc/messaging/telegram.py` — check
  `_get_log_file()` / `_LOG_FILE` there.
- The git helper in `merge.py` may overlap with `_rebase_dev_on_main()` or
  similar in `src/orc/git/core.py` — check before writing new code.
