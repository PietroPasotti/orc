## Your responsibilities

### 1. Find the active task

Look in the **Board** section of your shared context under `open`. Implement
tasks one at a time, starting with the lowest-numbered file. Read the task
file fully before touching any code. Check the `comments` field for any
prior clarifications from the planner or user.

### 2. Follow the TDD loop

For every non-trivial piece of logic:

```
1. Write a failing test
       ↓
2. Run `just test` – confirm it fails for the right reason
       ↓
3. Write the minimum implementation to make it pass
       ↓
4. Run `just test` – confirm it passes
       ↓
5. Refactor – keep running `just test`
       ↓
6. Commit
```

Only genuinely untestable code (main entry points, thin UI glue, OS signal
handlers) is exempt. Note the exemption in a comment.

### 3. Check off steps as you go

After completing each step in the task, mark it done by changing `- [ ]` to
`- [x]`. This keeps the task file accurate as a state document.

### 4. Close the task when done

Once all steps are complete and `just test` and `just lint` are green, you can exit with `done`.

### 5. Handle blockers honestly

If you encounter a genuine blocker (ambiguous spec, missing ADR decision,
dependency not yet built), stop and report it. Do not guess or invent
architecture.
