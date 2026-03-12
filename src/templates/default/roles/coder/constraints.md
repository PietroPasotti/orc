## Constraints

- Never modify `orc/work/board.yaml` except to check off completed steps within the active task entry.
- Never delete a task file — the orchestrator deletes it automatically after QA passes.
- Never modify ADRs. If an implementation decision diverges from an ADR,
  report it as a blocker so the planner can update the ADR first.
- Always leave `just test` green before reporting `done`.
- Do not add dependencies without checking `pyproject.toml` first and updating
  it via `uv add`.
