# Board management

Use the board management tool to create new tasks:

```bash
echo '<body-json>' | .orc/agent_tools/planner/create_task.py <agent-id> <task-title> <vision-file> [extra-file...]
```

The tool creates the task on the board and prints its filename.

### Body format (JSON on stdin)

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

```bash
echo '{
  "overview": "Add JWT-based authentication to the API.",
  "in_scope": ["login endpoint", "token refresh"],
  "out_of_scope": ["OAuth integration", "UI changes"],
  "steps": ["Write failing tests", "Implement auth middleware", "Wire into routes"],
  "notes": "See ADR-0042 for the chosen algorithm."
}' | .orc/agent_tools/planner/create_task.py planner-1 add-user-auth 0001-auth-vision.md
```

With an optional extra file (e.g. a new ADR):
```bash
echo '{...}' | .orc/agent_tools/planner/create_task.py planner-1 add-user-auth 0001-auth-vision.md docs/adr/0042-auth.md
```

## Task comments

Each task has a `comments` list for inter-agent and user-to-agent communication.
Check the comments on a task before creating follow-up tasks — they may contain
clarifications from previous agents or the user.
