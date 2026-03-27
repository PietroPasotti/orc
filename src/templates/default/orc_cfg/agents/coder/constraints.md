## Constraints

- Always try to test your code before reporting `done`
- **Run targeted tests first**: when iterating on a specific file, run only the
  relevant test file (e.g. `uv run pytest tests/test_foo.py -x -q`) instead of
  the full suite. Only run `just test` once at the end to confirm everything
  passes. This saves time and token budget.
- Do your best to follow the current project's conventions and patterns; your code should blend in with the existing codebase. Search for examples you can use as reference.
- Do not add dependencies unless you really need to. If unsure, ask.
- Always use `just test` / `just lint` or `uv run <tool>` to run project
  tools. Never invoke bare `pytest`, `mypy`, `ruff`, etc. — your worktree
  does not have its own virtual environment, so bare commands may resolve to
  the wrong Python or an unrelated venv.
