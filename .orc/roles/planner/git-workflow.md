## Git workflow

You work in the **dev worktree** (path given in the shared context under "Git workflow").

You are the **only agent that commits directly to `dev`**. After creating a task,
use the `create_task` MCP tool — it handles the commit automatically.

Do **not** craft the commit message by hand. All `git` commands must be run
from inside the dev worktree.

The board and task files are managed exclusively through the orc MCP server —
MCP tools handle all state access and git commits automatically. You do **not** need to stage
board or task files for git.
