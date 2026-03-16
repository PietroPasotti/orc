"""Full-screen Textual TUI for `orc run`.

Provides :class:`RunState`, :class:`AgentData`, :func:`render`, and
:func:`run_tui` — the building blocks for a real-time full-screen dashboard
shown when ``orc run`` is invoked with a TTY.

Usage::

    state = RunState(agents=[], features_done=0, telegram_ok=True,
                     backend="copilot", current_calls=0, max_calls=1)
    run_tui(state, lambda: dispatcher.run(maxloops=1))
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import rich.panel
import rich.table
from rich.console import Group, RenderableType
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Static

from orc.squad import AgentRole


@dataclass
class OrcData:
    """Data for the Orc card in the TUI panel."""

    agent_id: str
    """Unique agent identifier, e.g. ``coder-1``."""

    status: str
    """Current status string, e.g. ``running``."""

    task: str | None
    """What the orc is currently doing."""


@dataclass
class AgentData:
    """Data for the agent card in the TUI panel."""

    agent_id: str
    """Unique agent identifier, e.g. ``coder-1``."""

    role: str
    """Agent role: ``planner``, ``coder``, or ``qa``."""

    model: str
    """Model name used by this agent, e.g. ``copilot``."""

    status: str
    """Current status string, e.g. ``running``."""

    task_name: str | None
    """Board task the agent is working on, or ``refining`` for planners."""

    worktree: str
    """Path to the agent's git worktree (as a string for display)."""

    started_at: float
    """Monotonic timestamp when the agent was spawned."""

    details: str | None = None
    """Optional extra info shown below the task line (e.g. planner summary)."""


@dataclass
class RunState:
    """Global state displayed in the TUI panel."""

    agents: list[AgentData] = field(default_factory=list)
    """Live agent rows."""

    orc: OrcData | None = None
    """Orchestrator status, or ``None`` when not yet active."""

    features_done: int = 0
    """Number of feature branches merged into dev but not yet in main."""

    stuck_tasks: int = 0
    """Number of tasks currently in ``stuck`` status."""

    telegram_ok: bool = False
    """Whether the Telegram bot token is configured."""

    backend: str = "copilot"
    """AI backend identifier (``COLONY_AI_CLI`` env var)."""

    current_calls: int = 0
    """Agent sessions invoked so far."""

    max_calls: int = 0
    """Configured ``--maxcalls`` value (0 = unlimited)."""

    squad_name: str = ""
    """Squad profile name (e.g. ``"default"``)."""

    squad_repr: str = ""
    """Squad composition shorthand, e.g. ``"default (1-4-1)"``."""

    run_started_at: float = 0.0
    """Monotonic timestamp when the run started (for overall elapsed)."""


# Role → display colour mapping.
_ROLE_STYLE: dict[AgentRole, str] = {
    AgentRole.PLANNER: "cyan",
    AgentRole.CODER: "green",
    AgentRole.QA: "yellow",
}


def _elapsed(started_at: float) -> str:
    """Format seconds elapsed since *started_at* as ``"Xm Ys"``."""
    seconds = int(time.monotonic() - started_at)
    return f"{seconds // 60}m {seconds % 60}s"


def _agent_card(row: AgentData) -> rich.panel.Panel:
    """Render a single agent as a :class:`rich.panel.Panel`."""
    worktree_base = os.path.basename(row.worktree) or row.worktree
    task_name = row.task_name or ("refining" if row.role == AgentRole.PLANNER else "—")
    body = (
        f"status:  {row.status}\n"
        f"task:    {task_name}\n"
        f"wt:      {worktree_base}\n"
        f"elapsed: {_elapsed(row.started_at)}"
    )
    if row.details:
        body += f"\n{row.details}"
    return rich.panel.Panel(body, title=row.agent_id)


def _orc_card(data: OrcData) -> rich.panel.Panel:
    """Render a single orc as a :class:`rich.panel.Panel`."""
    body = f"status:  {data.status}\ntask:    {data.task or '—'}\n"
    return rich.panel.Panel(body, title=data.agent_id)


def _column_panel(role: str, rows: list[AgentData]) -> rich.panel.Panel:
    """Render a role column as a :class:`rich.panel.Panel`.

    The title shows ``"{role}  [{model}]"`` where *model* is the shared model
    across all rows, or ``"(mixed)"`` when rows use different models.
    """
    if rows:
        models = {r.model for r in rows}
        model_str = next(iter(models)) if len(models) == 1 else "(mixed)"
    else:
        model_str = ""

    role_style = _ROLE_STYLE.get(AgentRole(role.lower()), "white")
    title = f"[{role_style}]{role}[/{role_style}]"
    if model_str:
        title += rf"  \[{model_str}]"

    if not rows:
        body: RenderableType = "(idle)"
    else:
        body = Group(*[_agent_card(r) for r in rows])

    return rich.panel.Panel(body, title=title)


def render(state: RunState) -> RenderableType:
    """Build a grid Rich layout from *state*.

    The layout has:
    - A header row: agent call counter, backend, dev-ahead, Telegram status.
    - A body with a vertical split
        - Top section: orchestrator status
        - Bottom section has three columns: Planner | Coder | QA.
    """
    max_calls_str = str(state.max_calls) if state.max_calls > 0 else "∞"
    tg_str = "✓" if state.telegram_ok else "✗"
    stuck_str = f"  🔧 {state.stuck_tasks} stuck" if state.stuck_tasks > 0 else ""

    squad_str = f"  squad={state.squad_repr}" if state.squad_repr else ""
    runtime_str = f"  runtime {_elapsed(state.run_started_at)}" if state.run_started_at else ""
    header = (
        f"calls {state.current_calls}/{max_calls_str}  "
        f"{state.features_done} features done  "
        f"backend={state.backend}  "
        f"telegram={tg_str}"
        f"{stuck_str}"
        f"{squad_str}"
        f"{runtime_str}"
    )

    planners = [r for r in state.agents if r.role == AgentRole.PLANNER]
    coders = [r for r in state.agents if r.role == AgentRole.CODER]
    qa_agents = [r for r in state.agents if r.role == AgentRole.QA]

    workers_grid = rich.table.Table.grid(expand=True)
    workers_grid.add_column(ratio=1)
    workers_grid.add_column(ratio=1)
    workers_grid.add_column(ratio=1)
    workers_grid.add_row(
        _column_panel("Planner", planners),
        _column_panel("Coder", coders),
        _column_panel("QA", qa_agents),
    )

    wrapper = rich.table.Table(
        title=header,
        show_header=False,
        box=None,
        expand=True,
    )
    wrapper.add_column()
    if state.orc is not None:
        wrapper.add_row(_orc_card(state.orc))
    wrapper.add_row(workers_grid)

    return wrapper


class OrcApp(App[None]):
    """Full-screen Textual dashboard for ``orc run``."""

    BINDINGS = [Binding("q", "quit", "Quit")]

    def __init__(self, state: RunState, worker: threading.Thread) -> None:
        super().__init__()
        self._state = state
        self._worker = worker

    def compose(self) -> ComposeResult:
        yield Static(render(self._state), id="display")

    def on_mount(self) -> None:
        self.set_interval(0.25, self._refresh)

    def _refresh(self) -> None:
        self.query_one("#display", Static).update(render(self._state))
        if not self._worker.is_alive():
            self.exit()


def run_tui(state: RunState, run_fn: Callable[[], None]) -> None:
    """Run *run_fn* in a background thread while displaying the Textual TUI.

    Blocks until *run_fn* completes (or the user presses ``q``).  Any
    exception raised by *run_fn* is re-raised in the calling thread after
    the TUI exits.
    """
    exc_holder: list[BaseException] = []

    def _worker() -> None:
        try:
            run_fn()
        except BaseException as exc:  # noqa: BLE001
            exc_holder.append(exc)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    OrcApp(state, t).run()
    t.join()
    if exc_holder:
        raise exc_holder[0]
