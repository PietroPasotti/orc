# Board management

Use the board management tool to create new tasks:

```bash
.orc/agent_tools/planner/create_task.sh <task-title>
```

The tool reads the current counter from `board.yaml`, creates the task file
from template, adds it to the `open` list, and increments the counter.

After running the tool, edit the created task file to fill in the overview,
scope, steps, and notes.  Then use `publish_task.sh` to commit.
