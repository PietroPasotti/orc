# Task 0001 – Live TUI status view for `orc run`

## Overview

Implement the real-time TUI panel described in
`.orc/vision/0001-status-view.md`.  When `orc run` is invoked with a TTY,
replace the plain log stream with a live `rich`-powered panel that shows
per-agent progress and global system metadata while the dispatch loop runs.

Architecture is defined in `docs/adr/0001-live-tui-for-orc-run.md`.

## Scope

**In scope:**
- New `src/orc/tui.py` module (`RunState`, `AgentData`, `render()`,
  `live_context()`)
- `AgentProcess.model` field (new field; update all constructors)
- `DispatchCallbacks.on_agent_start` / `on_agent_done` optional callbacks
- `orc run --no-tui` flag + auto-disable when stdout is not a TTY
- `rich>=13` dependency in `pyproject.toml`
- Full unit tests for `tui.py` (`tests/test_tui.py`) and updated tests for
  changed modules

**Out of scope:**
- Changing the `orc status` command (static snapshot; separate concern)
- Persistent log files (agents already log to `~/.cache/orc/agents/`)
- Windows TTY detection edge cases beyond `sys.stdout.isatty()`

## Steps

- [ ] 1. Add `rich>=13` to `[project.dependencies]` in `pyproject.toml`.
         Run `uv sync` to update the lock file.

- [ ] 2. Create `src/orc/tui.py` with:
         - `AgentData` dataclass (fields: `agent_id`, `role`, `model`,
           `status`, `task_name`, `worktree`, `started_at`)
         - `RunState` dataclass (fields: `agents: list[AgentData]`,
           `dev_ahead: int`, `telegram_ok: bool`, `backend: str`,
           `current_loop: int`, `max_loops: int`)
         - `render(state: RunState) -> RenderableType` — builds a
           `rich.table.Table` with one row per agent plus a footer row for
           global metadata
         - `live_context(refresh_per_second: int = 4) -> rich.live.Live`
           — returns a pre-configured `Live` instance

- [ ] 3. Add `model: str` field to `AgentProcess` in `src/orc/pool.py`.
         Update every place that constructs an `AgentProcess` (the dispatcher
         and test fixtures) to pass `model=`.

- [ ] 4. Add two optional callbacks to `DispatchCallbacks` in
         `src/orc/dispatcher.py`:
         ```python
         on_agent_start: Callable[[AgentProcess], None] | None = None
         on_agent_done:  Callable[[AgentProcess, int], None] | None = None
         ```
         Call `on_agent_start(agent)` immediately after `pool.add(agent)`,
         and `on_agent_done(agent, rc)` immediately after a completed agent
         is removed from the pool.

- [ ] 5. Update `src/orc/cli/run.py`:
         - Add `--no-tui` flag (default `False`).
         - In `_run()`, detect `sys.stdout.isatty()` — if False or
           `--no-tui` is set, skip TUI entirely.
         - Otherwise, build a `RunState` initialised with empty agents,
           `backend=os.environ.get("COLONY_AI_CLI", "copilot")`,
           `telegram_ok=bool(os.environ.get("COLONY_TELEGRAM_TOKEN"))`,
           `current_loop=0`, `max_loops=maxloops`.
         - Create `on_agent_start` / `on_agent_done` closures that mutate
           `RunState.agents` (add / update / remove rows).
         - Update `current_loop` by hooking into the dispatcher's loop
           counter; the simplest approach is to expose `dispatcher.loop`
           (already accessible) and refresh it in a thin wrapper that
           calls `state.current_loop = dispatcher.loop` before each Live
           refresh.
         - Wrap `dispatcher.run(maxloops=maxloops)` inside
           `with live_context() as live: ...`, calling
           `live.update(render(state))` on each poll tick.
         - The `--no-tui` path continues to use `typer.echo` / structlog
           as before.

- [ ] 6. Write `tests/test_tui.py`:
         - Test `render()` with zero, one, and multiple `AgentData`s.
         - Test `render()` with each role (`planner`, `coder`, `qa`).
         - Test that `render()` reflects `RunState.current_loop`,
           `max_loops`, `backend`, `telegram_ok`, `dev_ahead`.
         - Test `live_context()` returns a `rich.live.Live` instance.

- [ ] 7. Update `tests/test_pool.py` to pass `model=` when constructing
         `AgentProcess` fixtures.

- [ ] 8. Update `tests/test_dispatcher.py` (and any other test that builds
         `DispatchCallbacks` or `AgentProcess`) to cover the new optional
         callbacks.

- [ ] 9. Update `tests/test_run.py` to cover:
         - `--no-tui` flag disables TUI.
         - Non-TTY stdout auto-disables TUI.
         - TUI path: `live_context` and `render` are called.

- [ ] 10. Run `just test` to confirm 100 % coverage.  Fix any gaps.

- [ ] 11. Run `just fmt` to apply ruff formatting, then `just lint` to
          verify no lint errors remain.

- [ ] 12. Commit:
          ```
          feat(tui): add live TUI status panel to orc run
          ```

## Notes

- **`rich.live.Live` and tests:** mock `orc.tui.live_context` in run tests
  to return a `MagicMock` context manager — avoids touching the real terminal.
- **`render()` test strategy:** use `rich.console.Console(file=io.StringIO())`
  and `console.print(render(state))` to capture rendered text without a TTY.
- **Loop counter:** `Dispatcher` already stores `self._loop: int`.  Expose it
  as a read-only property `Dispatcher.loop` so the run command can read it.
- **`dev_ahead` in TUI:** call the same `_dev_ahead_of_main()` helper that
  `status.py` already uses; cache the value and refresh every N seconds to
  avoid hammering git.
- **Model display:** for the `copilot` backend, show `"copilot"` as the model
  string since the CLI does not support model selection.
- **Coverage:** new module `tui.py` is 100 % covered by `test_tui.py`.
  The coverage config already has `fail_under = 100`.
