# Task 0003 – Nicer TUI: three-column role-card layout

## Overview

Redesign the `render()` function in `src/orc/tui.py` to replace the current
flat table with a richer layout that groups agents by role into three columns.
This implements the vision in `.orc/vision/0001-nicer-tui.md`.

Architecture baseline: `docs/adr/0001-live-tui-for-orc-run.md` (use
`rich.live.Live`; keep `RunState` / `AgentRow` as the data model).

## Scope

**In scope:**
- Replace the flat `rich.table.Table` in `render()` with a `rich.layout.Layout`
  (or equivalent `rich.columns` / `rich.panel` composition) that shows:
  1. A **header bar** at the top: loop counter, backend, `dev+N`, Telegram status.
  2. A **three-column main section**:
     - **Left** — "Planner" column: column header shows role + model; body
       contains one card per planner agent.
     - **Middle** — "Coder" column: column header shows role + model (or
       per-card if models differ); body contains one card per coder agent.
     - **Right** — "QA" column: same structure as Coder.
  3. Each **agent card** is a `rich.panel.Panel` containing key fields:
     agent ID, status, task name (or `—`), worktree basename, elapsed time.
  4. Column headers display the **shared role label** and, when all agents in
     the column share the same model, the **model name**; otherwise show
     `"(mixed)"`.
- Update `tests/test_tui.py` to cover the new layout (header bar content,
  3-column structure, per-card fields, mixed-model case, empty columns).
- Keep existing `AgentRow` and `RunState` dataclasses **unchanged** —
  the change is purely in rendering.
- Keep `live_context()` unchanged.

**Out of scope:**
- Changes to `AgentProcess`, `Dispatcher`, `pool.py`, or the run command.
- Persistent layout changes to `orc status` (static snapshot command).
- Windows TTY edge cases.

## Steps

- [ ] 1. Update `src/orc/tui.py`:

  a. Import `rich.layout`, `rich.panel`, `rich.columns` (or use `rich.table`
     creatively — pick whichever produces the cleanest card layout).

  b. Add a helper `_elapsed(started_at: float) -> str` that formats seconds
     into `"Xm Ys"`.

  c. Add a helper `_agent_card(row: AgentRow) -> rich.panel.Panel` that
     renders one agent's data as a `Panel`:
     - Title: `row.agent_id`
     - Body lines: status, task (or `—`), worktree basename, elapsed time.

  d. Add a helper `_column_panel(role: str, rows: list[AgentRow]) -> Panel`
     that:
     - Determines the model string: the shared model if all rows have the
       same model, otherwise `"(mixed)"`.
     - Renders a `Panel` titled `f"{role}  [{model}]"` whose body is a
       vertical stack of `_agent_card` outputs (use `rich.console.Group` or a
       `Table` with one column).
     - If `rows` is empty, the body should show `"(idle)"`.

  e. Rewrite `render(state: RunState) -> RenderableType`:
     - Build the header string:
       ```
       loop {current}/{max}  dev+{dev_ahead}  backend={backend}  telegram={✓|✗}
       ```
     - Split `state.agents` into three lists by role:
       `planners`, `coders`, `qa_agents`.
     - Build three column panels via `_column_panel`.
     - Compose header + three columns into a single renderable.
       Recommended approach: use a two-row `rich.table.Table` (header row +
       columns row) — this avoids `rich.layout.Layout`'s fixed-height
       requirement and works well in non-TTY test contexts.

- [ ] 2. Update `tests/test_tui.py`:

  a. Test `_elapsed()`: 0 s → `"0m 0s"`, 90 s → `"1m 30s"`, 3661 s → `"61m 1s"`.

  b. Test `_agent_card()`: verify the panel title is the agent ID and the
     body contains status, task name, worktree basename, and elapsed string.

  c. Test `_column_panel()`:
     - Empty rows → body contains `"(idle)"`.
     - Single row → title includes role and model.
     - Multiple rows with the same model → title shows that model.
     - Multiple rows with different models → title shows `"(mixed)"`.

  d. Test `render()`:
     - With an empty `RunState` (no agents): verify it renders without error,
       header contains `"loop 0/∞"`, all three columns present with `"(idle)"`.
     - With one planner, two coders, one QA: verify the rendered output
       (capture via `rich.console.Console(file=io.StringIO())`) contains
       all agent IDs and `"Planner"`, `"Coder"`, `"QA"` column titles.
     - Verify `telegram_ok=True` → `"✓"` in header,
       `telegram_ok=False` → `"✗"` in header.
     - Verify `max_loops=0` → `"∞"` in header.

- [ ] 3. Run `just test` — confirm 100 % coverage and all tests pass.

- [ ] 4. Run `just fmt` then `just lint` — fix any issues.

- [ ] 5. Commit:
       ```
       feat(tui): redesign live panel with three-column role-card layout
       ```

## Notes

- **`rich.layout.Layout` vs table composition:** `Layout` requires fixed
  terminal height, which makes it awkward in tests.  Prefer composing via
  a single outer `Table` with two rows (header + three-column body) or via
  `rich.columns.Columns` wrapped in a `Panel`.  Both render correctly under
  `Console(file=io.StringIO())`.
- **`rich.console.Group`:** useful for stacking multiple renderables
  vertically inside a single cell or Panel body.
- **Test isolation:** tests should not import `time.monotonic` directly —
  patch `orc.tui.time` (or pass `started_at` values as constants) so
  elapsed-time output is deterministic.
- **Remove vision doc:** after this task is merged, the planner should close
  `.orc/vision/0001-nicer-tui.md` via the standard vision-closing procedure.
