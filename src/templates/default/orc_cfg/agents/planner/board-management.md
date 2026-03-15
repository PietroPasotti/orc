# Board management

Use the board management tool to create new tasks by calling the `create_task` MCP tool:

Call the `create_task` tool with parameters: `task_title="<title>"`, `vision_file="<vision-filename>"`, `body="<json-body>"`, `extra_files=[...]`

The tool creates the task on the board and prints its filename.

### Body format (JSON)

```json
{
  "overview":      "<free-form description of what is being built and why>",
  "in_scope":      ["item 1", "item 2"],
  "out_of_scope":  ["item 1"],
  "steps":         ["step 1", "step 2"],
  "notes":         "<optional: blockers, design decisions, tips for the coder>"
}
```

### Example

To create a task titled "add-user-auth" from vision file "0001-auth-vision.md":

Call `create_task` with:
- `task_title="add-user-auth"`
- `vision_file="0001-auth-vision.md"`
- `body='{"overview": "Add JWT-based authentication to the API.", "in_scope": ["login endpoint", "token refresh"], "out_of_scope": ["OAuth integration", "UI changes"], "steps": ["Write failing tests", "Implement auth middleware", "Wire into routes"], "notes": "See ADR-0042 for the chosen algorithm."}'`

With optional extra files (e.g. a new ADR), add them to the `extra_files` parameter:
```
extra_files=["docs/adr/0042-auth.md"]
```

## Task comments

Each task has a `comments` list for inter-agent and user-to-agent communication.
Check the comments on a task before creating follow-up tasks — they may contain
clarifications from previous agents or the user.
