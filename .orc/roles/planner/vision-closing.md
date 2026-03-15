# Closing completed visions

## When to close a vision

Inspect the "Pending visions" section in your shared context each run.
A vision is ready to close when every feature it describes has been
implemented and merged.

## How to close

Call the `close_vision` MCP tool:

- `vision_file` — vision filename, e.g. `"0001-shark-fleet.md"`
- `summary` — 2–4 sentence description of what was accomplished
- `task_files` — optional list of task filenames that implement this vision

The tool appends a changelog entry to `.orc/orc-CHANGELOG.md`, deletes the
vision file from the project cache, and returns a confirmation.

## When you are done

You are done when:
- All implemented visions are closed.
- Remaining visions have been translated into tasks and/or ADRs.
- All code `#TODO` and `#FIXME` comments have been translated into tasks.
- All tasks are implemented and there are no remaining tasks with `planned`, `in-progress`, or `in-review` status.
