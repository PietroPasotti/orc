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

import rich.console
import rich.panel
import rich.table
from rich.console import Group, RenderableType
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from orc.engine.dispatcher import DispatcherPhase
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

    role: AgentRole
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

    backend: str = "internal"
    """AI backend identifier."""

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

    dispatcher_phase: DispatcherPhase = DispatcherPhase.RUNNING
    """Current lifecycle phase of the dispatcher."""

    agent_calls: dict[AgentRole, int] = field(
        default_factory=lambda: {role: 0 for role in AgentRole}
    )
    """Number of agent sessions invoked, keyed by role."""

    def _agents_table(self, agents_by_role: dict[AgentRole, list[str] | int]) -> rich.table.Table:
        """Render the live agents as a Rich table."""
        agents_live_table = rich.table.Table(box=None, show_header=False, expand=False)
        for role in AgentRole:
            ids = agents_by_role.get(role)
            match ids:
                case int() as count:
                    ids_str = str(count)
                case list() as ids:
                    ids_str = ", ".join(sorted(ids)) if ids else "(none)"
                case _:
                    ids_str = "(n/a)"
            role_style = _ROLE_STYLE.get(role, "white")
            role_str = f"[{role_style}]{role.capitalize()}[/{role_style}]"
            agents_live_table.add_row(role_str, ids_str)
        return agents_live_table

    def _summary(
        self, error: BaseException | None = None, elapsed_seconds: int = 0
    ) -> dict[str, str | RenderableType]:
        max_calls_str = str(self.max_calls) if self.max_calls > 0 else "∞"

        agents_live: dict[AgentRole, list[str]] = {}
        for agent in self.agents:
            agents_live.setdefault(agent.role, list()).append(agent.agent_id)

        labels = {
            "status": f"✗ error ({error.__class__.__name__})" if error else "✓ ok",
            "draining": "yes" if self.draining else "no",
            "backend": self.backend,
            "duration": _format_duration(elapsed_seconds),
            "calls": f"{self.current_calls}/{max_calls_str}",
            "live agents": self._agents_table(agents_live),
            "agent calls": self._agents_table(self.agent_calls),
            "completed features": self.features_done,
        }
        if self.stuck_tasks > 0:
            labels["stuck"] = self.stuck_tasks
        if error:
            labels["error"] = f"{type(error).__name__}: {error}"
        if self.squad_name:
            labels["squad"] = self.squad_repr

        for k, v in labels.items():
            if isinstance(v, int | float | bool):
                labels[k] = str(v)
        return labels

    def rich_summary(
        self, error: BaseException | None = None, elapsed_seconds: int = 0
    ) -> RenderableType:
        """Return a Rich-renderable summary string with current run statistics."""
        labels = self._summary(error=error, elapsed_seconds=elapsed_seconds)

        table = rich.table.Table(box=None, show_header=False, expand=False)
        for key, value in labels.items():
            key_str = f"[bold cyan]{key}[/]"
            table.add_row(key_str, value if isinstance(value, str) else value)
        return table

    @property
    def draining(self) -> bool:
        """Whether the dispatcher is in drain mode (derived from :attr:`dispatcher_phase`)."""
        return self.dispatcher_phase is DispatcherPhase.DRAINING


# Role → display colour mapping.
_ROLE_STYLE: dict[AgentRole, str] = {
    AgentRole.PLANNER: "cyan",
    AgentRole.CODER: "green",
    AgentRole.QA: "yellow",
    AgentRole.MERGER: "magenta",
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
    tg_icon = "✓" if state.telegram_ok else "✗"
    tg_style = "green" if state.telegram_ok else "red"

    sep = " [dim]│[/dim] "
    parts: list[str] = [
        f"[bold]calls[/bold] {state.current_calls}/{max_calls_str}",
        f"[green]{state.features_done} features done[/green]",
        f"[dim]backend={state.backend}[/dim]",
        f"telegram=[{tg_style}]{tg_icon}[/{tg_style}]",
    ]
    if state.stuck_tasks > 0:
        parts.append(f"[bold red]🔧 {state.stuck_tasks} stuck[/bold red]")
    if state.squad_repr:
        parts.append(f"[dim]squad={state.squad_repr}[/dim]")
    if state.run_started_at:
        parts.append(f"[dim]runtime {_elapsed(state.run_started_at)}[/dim]")
    if state.draining:
        parts.append("[bold yellow]⏳ draining…[/bold yellow]")

    header = sep.join(parts)

    planners = [r for r in state.agents if r.role == AgentRole.PLANNER]
    coders = [r for r in state.agents if r.role == AgentRole.CODER]
    qa_agents = [r for r in state.agents if r.role == AgentRole.QA]
    mergers = [r for r in state.agents if r.role == AgentRole.MERGER]

    workers_grid = rich.table.Table.grid(expand=True)
    workers_grid.add_column(ratio=1)
    workers_grid.add_column(ratio=1)
    workers_grid.add_column(ratio=1)
    workers_grid.add_column(ratio=1)
    workers_grid.add_row(
        _column_panel("Planner", planners),
        _column_panel("Coder", coders),
        _column_panel("Merger", mergers),
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


class QuitModal(ModalScreen[str]):
    """Modal dialog shown when the user presses Ctrl+Q.

    Offers two choices: drain pending tasks (recommended) or abort immediately.
    Returns ``"drain"`` or ``"abort"`` to the parent app.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    CSS = """
    QuitModal {
        align: center middle;
    }
    #quit-dialog {
        width: 60;
        height: auto;
        border: thick $error;
        background: $surface;
        padding: 1 2;
    }
    #quit-dialog Static {
        width: 100%;
        content-align: center middle;
        margin-bottom: 1;
    }
    #quit-dialog Button {
        width: 100%;
        margin-top: 1;
    }
    #btn-drain {
        background: $success;
    }
    #btn-abort {
        background: $error;
    }
    """

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical

        with Vertical(id="quit-dialog"):
            yield Static("⏹  Stop orc run?")
            yield Button(
                "⏳ Drain — finish running agents, then exit (Recommended)",
                id="btn-drain",
                variant="success",
            )
            yield Button(
                "⚠  Abort — kill all agents immediately (DANGEROUS)",
                id="btn-abort",
                variant="error",
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-drain":
            self.dismiss("drain")
        elif event.button.id == "btn-abort":
            self.dismiss("abort")

    def action_cancel(self) -> None:
        self.dismiss("")


class OrcApp(App[None]):
    """Full-screen Textual dashboard for ``orc run``."""

    BINDINGS = [Binding("ctrl+q", "request_quit", "Ctrl+Q Exit", priority=True)]

    CSS = """
    #footer-bar {
        dock: bottom;
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        state: RunState,
        worker: threading.Thread,
        *,
        on_drain: Callable[[], None] | None = None,
        on_abort: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._state = state
        self._worker = worker
        self._on_drain = on_drain
        self._on_abort = on_abort

    def compose(self) -> ComposeResult:
        yield Static(render(self._state), id="display")
        yield Static(" Ctrl+Q to exit", id="footer-bar")

    def on_mount(self) -> None:
        self.set_interval(0.25, self._refresh)

    def action_request_quit(self) -> None:
        """Press Ctrl+Q: show the quit modal.

        If a drain callback is configured, present a choice between draining
        and aborting.  Without a callback, fall back to immediate exit.
        """
        if self._on_drain is not None:
            self.push_screen(QuitModal(), callback=self._handle_quit_choice)
        else:
            self.exit()

    def _handle_quit_choice(self, choice: str | None) -> None:
        if choice == "drain":
            if self._on_drain is not None:
                self._on_drain()
        elif choice == "abort":
            if self._on_abort is not None:
                self._on_abort()
            self.exit()

    def _refresh(self) -> None:
        self.query_one("#display", Static).update(render(self._state))
        if not self._worker.is_alive():
            self.exit()


def _format_duration(seconds: float) -> str:
    """Format *seconds* as ``"Xm Ys"``."""
    total = int(seconds)
    return f"{total // 60}m {total % 60}s"


def run_tui(
    state: RunState,
    run_fn: Callable[[], None],
    *,
    on_drain: Callable[[], None] | None = None,
    on_abort: Callable[[], None] | None = None,
) -> None:
    """Run *run_fn* in a background thread while displaying the Textual TUI.

    Blocks until *run_fn* completes (or the user presses ``Ctrl+Q``).  Any
    exception raised by *run_fn* is re-raised in the calling thread after
    the TUI exits.  A compact summary is printed to stdout before returning
    (or re-raising).

    When *on_drain* is provided, pressing ``Ctrl+Q`` opens a modal offering
    a choice between draining (letting running agents finish) and aborting
    (killing agents immediately).  The app exits after the worker thread
    completes.
    """
    exc_holder: list[BaseException] = []
    start = time.monotonic()

    def _worker() -> None:
        try:
            run_fn()
        except BaseException as exc:  # noqa: BLE001
            exc_holder.append(exc)

    try:
        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        OrcApp(state, t, on_drain=on_drain, on_abort=on_abort).run()
        t.join()
    finally:
        elapsed = time.monotonic() - start
        error = exc_holder[0] if exc_holder else None
        rich.console.Console().print(state.rich_summary(error=error, elapsed_seconds=elapsed))

    if exc_holder:
        raise exc_holder[0]
