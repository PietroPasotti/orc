# Closing completed visions

## When to close a vision

Inspect `.orc/vision/` each run.  A vision is ready to close when every
feature it describes has been implemented and merged.

## How to close

```bash
.orc/agent_tools/planner/close_vision.py <vision-file> "<summary>" [task-file...]
```

The tool appends a changelog entry to `.orc/orc-CHANGELOG.md`, deletes the
vision file, and prints a confirmation.

## When you are done

You are done when:
- All implemented visions are closed.
- Remaining visions have been translated into tasks and/or ADRs.
- All code `#TODO` and `#FIXME` comments have been translated into tasks.
- All tasks are implemented and the `open` list is empty.
