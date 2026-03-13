# Task 0008 – Engine robustness: ctrl+C guard and dispatcher error handling

## Overview

Two `#TODO` / `#FIXME` comments identify robustness gaps in the engine:

1. **`src/orc/cli/run.py:126`** – The main run loop is not protected against
   accidental `Ctrl+C`.  An interrupt at the wrong moment can leave stale
   feature branches and partially-merged board state.
2. **`src/orc/engine/dispatcher.py:443`** – The `do_close_board()` call inside
   `_dispatch()` is not wrapped in a `try/except`.  A failure here silently
   swallows errors on the critical dispatch path.

## Scope

**In scope:**
- `src/orc/cli/run.py`: wrap the `dispatcher.run()` call in a
  `try/except KeyboardInterrupt` block.  On interrupt, print a clear warning
  (e.g. `"⚠ Interrupted. Dev branch and board may be in a partial state.
  Run orc run again to resume."`) and exit with a non-zero code.  Do **not**
  silently swallow the interrupt or attempt automatic cleanup.
- `src/orc/engine/dispatcher.py`: wrap `self.workflow.do_close_board(task_name)`
  (line ~443) in a `try/except Exception` block.  Log the error via structlog
  (`logger.exception`) and continue the dispatch loop — a failed board-close
  should not crash the entire dispatcher.  Apply the same pattern to any
  other unguarded calls in `_dispatch()` that the comment indicates
  (`"Do the same for all calls out of _dispatch, this is a critical path."`).
- Remove the `# TODO` and `# FIXME` comments from both files once implemented.
- Add / update tests:
  - `tests/test_run.py`: test that a `KeyboardInterrupt` during
    `dispatcher.run()` prints the warning and exits non-zero.
  - `tests/test_dispatcher.py`: test that `do_close_board()` failure is
    logged and does not propagate.

**Out of scope:**
- Automatic rollback or cleanup on interrupt.
- Handling `SIGTERM` or other signals.
- Changes to any other command (`status`, `merge`, etc.).

## Steps

- [ ] 1. **`src/orc/cli/run.py`** – protect the run loop:
  ```python
  try:
      dispatcher.run(maxloops=maxloops)
  except KeyboardInterrupt:
      typer.echo(
          "\n⚠ Interrupted. The dev branch and board may be in a partial "
          "state. Run `orc run` again to resume.",
          err=True,
      )
      raise typer.Exit(code=1)
  ```
  Remove the `# TODO` comment (line 126).

- [ ] 2. **`src/orc/engine/dispatcher.py`** – wrap `do_close_board()` and
  other unguarded critical-path calls in `_dispatch()`:
  ```python
  try:
      self.workflow.do_close_board(task_name)
  except Exception:
      logger.exception("do_close_board failed", task=task_name)
  ```
  Apply the same pattern to any other `# FIXME` / `# TODO` comments on
  the same critical path inside `_dispatch()`.
  Remove the `# FIXME` comment (line 443).

- [ ] 3. **`tests/test_run.py`** – add test:
  - `test_run_keyboard_interrupt_prints_warning()`: mock
    `dispatcher.run()` to raise `KeyboardInterrupt`; assert the warning
    message is printed and the exit code is 1.

- [ ] 4. **`tests/test_dispatcher.py`** – add test:
  - `test_dispatch_close_board_failure_is_logged()`: mock
    `workflow.do_close_board()` to raise an exception; assert the
    dispatcher logs the error and does not re-raise.

- [ ] 5. Run `just test` and `just lint`; fix any failures.

- [ ] 6. Commit:
  ```
  fix(engine): guard run loop against ctrl+C; catch dispatcher close-board errors
  ```

## Notes

- Sources: `src/orc/cli/run.py:126` and `src/orc/engine/dispatcher.py:443`.
- The dispatcher comment reads: "Do the same for all calls out of `_dispatch`,
  this is a critical path." — scan the surrounding code for other unguarded
  calls and wrap them too.
- Keep the `KeyboardInterrupt` handler minimal: no git cleanup, no board
  rollback.  The run-loop is designed to be idempotent on re-run.
