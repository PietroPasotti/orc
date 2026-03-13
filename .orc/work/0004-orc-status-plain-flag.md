# 0004 – `orc status --plain`: non-interactive output flag

## Overview

`orc status` currently auto-detects whether stdout is a TTY and launches a
full Textual TUI if so.  That makes it unusable in CI, scripts, and AI-agent
subprocesses.  This task adds a `--plain` flag that forces the plain-text
output path even when running in an interactive terminal.

Vision doc: `.orc/vision/0004-orc-status-plain-output.md`

## Scope

**In scope:**
- Add `--plain: bool = False` option to the `status` CLI command in
  `src/orc/cli/status.py`.
- When `--plain` is `True`, skip the `_is_tty()` check and call `_status()`
  directly.
- Update `tests/test_status.py` to cover:
  - `--plain` skips the TUI even when `_is_tty()` returns `True`.
  - `--plain` produces the same plain-text output as the non-TTY path.
  - Without `--plain`, the existing TTY → TUI / non-TTY → plain behaviour is
    unchanged.

**Out of scope:**
- `--json` output format.
- Streaming / watch mode.
- Any changes to the TUI itself.

## Steps

- [ ] 1. **`src/orc/cli/status.py`** – extend the `status()` command:
  ```python
  @app.command()
  def status(
      squad: Annotated[str, typer.Option("--squad", ...)] = "default",
      plain: Annotated[
          bool,
          typer.Option("--plain", help="Print plain text without launching the TUI."),
      ] = False,
  ) -> None:
      """Print current workflow state without running any agent."""
      if not plain and _is_tty():
          from orc.tui.status_tui import run_status_tui
          run_status_tui(squad=squad)
      else:
          return _status(squad=squad)
  ```

- [ ] 2. **`tests/test_status.py`** – add / extend tests:
  - `test_plain_flag_bypasses_tui_when_tty()`: patch `_is_tty` to return
    `True`; invoke `status --plain`; assert TUI is **not** launched and
    `_status` is called.
  - `test_plain_flag_produces_plain_output()`: invoke `status --plain` with a
    real (or mocked) board/git state; assert expected sections appear in
    stdout.
  - Ensure existing `test_status_tui_launched_when_isatty()` still passes
    (without `--plain`, TUI is launched when TTY).

- [ ] 3. Run `just test` and `just lint`; fix any failures.

- [ ] 4. Commit:
  ```
  feat(status): add --plain flag to skip TUI in interactive terminals
  ```

## Notes

- The existing non-TTY path (`_status()`) already produces the correct output;
  `--plain` simply forces that path.
- No changes to `_status()` itself are needed.
- The `status *args` recipe in `src/templates/default/justfile` and
  `.orc/justfile` already passes `*args` through, so `just orc status --plain`
  works without any justfile changes.
