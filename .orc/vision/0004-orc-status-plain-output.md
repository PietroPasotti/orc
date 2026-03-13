# orc status: plain/non-interactive output mode

## What

Add a `--plain` flag to `orc status` that prints the current workflow state as
plain text to stdout and exits, without launching the TUI.

## Why

`orc status` currently opens a full Textual TUI, which requires a real TTY.
This makes it unusable in:
- non-interactive shells (CI, scripts, pipes)
- AI agents / automation that call orc as a subprocess
- quick terminal checks where spinning up a TUI is overkill

## Constraints

- `orc status --plain` exits immediately after printing; no event loop
- Output should cover the same information as the TUI: board state (open/done tasks), agent last-known states, git branch summary (dev ahead of main by N commits)
- No new dependencies; use existing rich or plain print

## Out of scope

- JSON output format (could be a follow-up `--json` flag)
- Streaming / watch mode for plain output
