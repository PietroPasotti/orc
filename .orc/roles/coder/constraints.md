## Constraints

- Never modify the board directly — the orchestrator and agent tools manage board state.
- Never delete a task file — the orchestrator deletes it automatically after QA passes.
- Never modify ADRs. If an implementation decision diverges from an ADR,
  report it as a blocker so the planner can update the ADR first.
- Always leave `just test` green before reporting `done`.
- Do not add dependencies without checking `pyproject.toml` first and updating
  it via `uv add` and unless really necessary.
