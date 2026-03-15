# Closing completed visions

## When to close a vision

Inspect the "Pending visions" section in your shared context each run.
A vision is ready to close when every feature it describes has been
implemented and merged.

## How to close

Call the `close_vision` MCP tool with parameters: `vision_file="<vision-filename>"`, `summary="<summary>"`, `task_files=[...]`

The tool closes the vision and prints a confirmation.

## When you are done

You are done when:
- All visions are closed, i.e. have been translated into tasks and/or ADRs.
- All code `#TODO` and `#FIXME` comments have been translated into tasks.