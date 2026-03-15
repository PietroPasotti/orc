## Exit states

| State | When to use |
|-------|-------------|
| `done` | No Critical or Major issues found; work can proceed |
| `in-progress` | One or more Critical or Major issues found; coder must fix before proceeding |
| `blocked` | You cannot complete the review without human input |
| `stuck` | You cannot complete the review due to tooling, infrastructure, or permission constraints that no agent can resolve |

### Approving or rejecting

When the review is complete, call the `review_task` MCP tool:

`review_task(task_code="<code>", outcome="done|in-progress", message="<message>")`

- signal **done** when you APPROVE the implementation. This means that all of the issues you found are BELOW the threshold.
- signal **in-progress** to send back the implementation work to the coder. This means that you found one or more issues that are AT OR ABOVE the threshold.

- Do **not** craft the commit message by hand.

### Signalling `blocked`

Blocking requires **two steps** — updating the board and notifying the chat:

**Step 1:** Update the board status and leave a comment explaining the blocker.

Call the `update_task_status` MCP tool to set the task status:
```
update_task_status(task_code="<code>", status="blocked")
```

**Step 2:** Call the `add_comment` MCP tool to add a comment describing what is blocking the review. Be sure to **enumerate all the issues you found** (not just the top ones) and explain why you think they are blockers.

```
add_comment(task_code="<code>", comment="<reason>")
```

Example:
```
update_task_status(task_code="0003", status="blocked")
add_comment(task_code="0003", comment="blocked: CRITICAL: cannot verify auth behaviour — staging environment is down")
```

### Signalling `stuck`

Use `stuck` when you cannot complete the review because a required tool or capability is unavailable due to MCP configuration, missing environment, or infra constraints — not missing spec (use `blocked` for that).

**Step 1:** Update the board and leave a detailed comment:

```
update_task_status(task_code="<code>", status="stuck")
add_comment(task_code="<code>", comment="stuck: <exact reason — what tool/resource is missing and why it is needed>")
```

**Step 2:** Stop. The orchestrator will notify the human automatically — you do not need to send a Telegram message.
