## Git workflow

You work in the **feature worktree** (path given in the shared context under "Git workflow").
The feature worktree is already checked out on the feature branch (`feat/NNNN-task-title`),
so you can inspect the changes directly using the `git` CLI.

**Do NOT merge the feature branch yourself.** The orchestrator handles the merge
automatically once you signal approval via the `review_task` MCP tool.
