## Constraints

- Always try to test your code before reporting `done`
- Do your best to follow the current project's conventions and patterns; your code should blend in with the existing codebase. Search for examples you can use as reference.
- Do not add dependencies unless you really need to. If unsure, ask.
- Always use `just test` / `just lint` or `uv run <tool>` to run project
  tools. Never invoke bare `pytest`, `mypy`, `ruff`, etc. — your worktree
  does not have its own virtual environment, so bare commands may resolve to
  the wrong Python or an unrelated venv.
