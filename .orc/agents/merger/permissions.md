# Permissions

The merger agent has access to:
- All **orc MCP tools** (board read/write, task management).
- **File read/write** — for resolving merge conflicts.
- **Git operations** — merge, checkout, status, log, diff, add, commit.
- **Shell access** — for running build/test commands post-merge.

The merger agent must **not**:
- Push to any remote.
- Delete or create branches (the orchestrator handles cleanup).
- Modify files outside the dev worktree.
