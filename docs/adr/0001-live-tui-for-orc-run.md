# ADR-0001 – Live TUI for `orc run`

**Status:** Accepted  
**Date:** 2026-03-11

---

## Context

`orc run` currently produces plain log lines.  The vision document
`.orc/vision/0001-status-view.md` asks for a real-time TUI panel that
surfaces per-agent progress (role, model, status, current task, runtime,
worktree) while the dispatch loop is running, plus global metadata
(dev-vs-main, Telegram link, backend, loop counter).

Key constraints:
- 100 % test coverage is enforced; the TUI layer must be fully unit-testable.
- The dispatcher runs in a single thread (poll loop) — no concurrency
  primitives are needed.
- The `claude` backend supports model selection; the `copilot` backend does
  not expose it.

---

## Decision

### 1. TUI framework: `rich.live.Live`

Add **`rich`** as a runtime dependency.  Use `rich.live.Live` + `rich.table`
to render a refreshing panel inside the terminal.

Rationale:
- `rich` is the de-facto standard for Python TUI / pretty-printing; well
  maintained and widely used.
- `rich.live.Live` is a lightweight context manager — no event loop, no
  threads, no separate process.
- A `Textual` full-screen TUI would be over-engineered for a status panel
  that sits beside log output.
- `rich` is already an indirect dependency of `structlog` (via `rich`
  pretty-printing); making it explicit is a no-op for most installs.

### 2. State container: `RunState` dataclass (`src/orc/tui.py`)

Introduce a **`RunState`** dataclass (new module `src/orc/tui.py`) that holds
all data the TUI needs:

```python
@dataclass
class AgentData:
    agent_id: str        # e.g. "coder-1"
    role: str            # "planner" | "coder" | "qa"
    model: str           # e.g. "claude-sonnet-4.6" or "copilot"
    status: str          # "running" | "idle" | "blocked"
    task_name: str | None
    worktree: str | None
    started_at: float    # monotonic

@dataclass
class RunState:
    agents: list[AgentData]
    dev_ahead: int            # commits dev is ahead of main
    telegram_ok: bool
    backend: str              # "copilot" | "claude"
    current_loop: int
    max_loops: int            # 0 = unlimited
```

`tui.py` also exposes a `render(state: RunState) -> rich.console.RenderableType`
function that builds the Rich panel, and a `live_context()` helper that
returns a configured `rich.live.Live` instance.

All rendering logic lives in `tui.py` and is tested without touching the
terminal (mock `rich.live.Live` or use `rich.console.Console(file=...)`).

### 3. State updates: callbacks injected into `Dispatcher`

The `Dispatcher` already accepts a `DispatchCallbacks` bag.  Add two new
optional callbacks:

```python
on_agent_start: Callable[[AgentProcess], None] | None
on_agent_done:  Callable[[AgentProcess, int], None] | None
```

The `run` command (or a thin wrapper) creates a `RunState`, passes
`on_agent_start` / `on_agent_done` closures that mutate it, and wraps the
`Dispatcher.run()` call in a `Live` context that refreshes on each poll
interval.

### 4. `AgentProcess` enriched with `model`

Add a `model: str` field to `AgentProcess` so the pool knows which model each
agent is using.  The dispatcher already has the squad config when it spawns
agents; it passes `squad_cfg.model(role)` at spawn time.

### 5. `orc run --no-tui` escape hatch

Add a `--no-tui` flag to `orc run` to fall back to the current plain-log
output (useful in CI or when stdout is not a TTY).  When `sys.stdout.isatty()`
is `False`, `--no-tui` is implied.

---

## Consequences

- `rich>=13` is added to `[project.dependencies]` in `pyproject.toml`.
- `src/orc/tui.py` is a new module with its own test file `tests/test_tui.py`.
- `AgentProcess` gains a `model` field — all existing call sites that
  construct `AgentProcess` must pass `model=`.
- `DispatchCallbacks` gains two optional fields (`on_agent_start`,
  `on_agent_done`) — backwards-compatible since they default to `None`.
- The `run` command's terminal output changes when stdout is a TTY; the
  plain-log path is unchanged otherwise.
