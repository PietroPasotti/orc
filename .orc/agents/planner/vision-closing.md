# Closing visions

## When to close a vision

Close a vision **immediately after creating all tasks from it**.
Do NOT wait for the tasks to be implemented — closing a vision means
"the planner has finished breaking this vision down into tasks."

## How to close

Call the `close_vision` MCP tool:
```
close_vision(vision_file="<vision-filename>", summary="<2-4 sentence summary>", task_files=["0046-foo.md", "0047-bar.md"])
```

## Workflow per vision

1. Call `get_vision(vision_filename="...")` to read the full document.
2. Call `create_task(...)` for each task derived from the vision.
3. Call `close_vision(...)` listing all task files you just created.
4. Move on to the next pending vision.

**Important:** Never skip step 3. If you created tasks from a vision,
you MUST close it before finishing your session.

## When you are done

You are done when:
- All pending visions have been closed (translated into tasks).
- All code `#TODO` and `#FIXME` comments have been translated into tasks.