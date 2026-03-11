"""Live TUI panel for `orc run`.

Provides :class:`RunState`, :class:`AgentRow`, :func:`render`, and
:func:`live_context` — the building blocks for a real-time Rich dashboard
shown when ``orc run`` is invoked with a TTY.

Usage::

    state = RunState(agents=[], dev_ahead=0, telegram_ok=True,
                     backend="copilot", current_loop=0, max_loops=1)
    with live_context() as live:
        live.update(render(state))
        # ... mutate state ...
        live.update(render(state))
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import rich.live
import rich.panel
import rich.table
from rich.console import Group, RenderableType


@dataclass
class AgentRow:
    """A single row in the TUI agent table."""

    agent_id: str
    """Unique agent identifier, e.g. ``coder-1``."""

    role: str
    """Agent role: ``planner``, ``coder``, or ``qa``."""

    model: str
    """Model name used by this agent, e.g. ``copilot``."""

    status: str
    """Current status string, e.g. ``running``."""

    task_name: str | None
    """Board task the agent is working on, or ``None`` for planners."""

    worktree: str
    """Path to the agent's git worktree (as a string for display)."""

    started_at: float
    """Monotonic timestamp when the agent was spawned."""


@dataclass
class RunState:
    """Global state displayed in the TUI panel."""

    agents: list[AgentRow] = field(default_factory=list)
    """Live agent rows."""

    dev_ahead: int = 0
    """Commits dev is ahead of main."""

    telegram_ok: bool = False
    """Whether the Telegram bot token is configured."""

    backend: str = "copilot"
    """AI backend identifier (``COLONY_AI_CLI`` env var)."""

    current_loop: int = 0
    """Dispatch cycles completed so far."""

    max_loops: int = 0
    """Configured ``--maxloops`` value (0 = unlimited)."""


# Role → display colour mapping.
_ROLE_STYLE: dict[str, str] = {
    "planner": "cyan",
    "coder": "green",
    "qa": "yellow",
}


def _elapsed(started_at: float) -> str:
    """Format seconds elapsed since *started_at* as ``"Xm Ys"``."""
    seconds = int(time.monotonic() - started_at)
    return f"{seconds // 60}m {seconds % 60}s"


def _agent_card(row: AgentRow) -> rich.panel.Panel:
    """Render a single agent as a :class:`rich.panel.Panel`."""
    worktree_base = os.path.basename(row.worktree) or row.worktree
    body = (
        f"status:  {row.status}\n"
        f"task:    {row.task_name or '—'}\n"
        f"wt:      {worktree_base}\n"
        f"elapsed: {_elapsed(row.started_at)}"
    )
    return rich.panel.Panel(body, title=row.agent_id)


def _column_panel(role: str, rows: list[AgentRow]) -> rich.panel.Panel:
    """Render a role column as a :class:`rich.panel.Panel`.

    The title shows ``"{role}  [{model}]"`` where *model* is the shared model
    across all rows, or ``"(mixed)"`` when rows use different models.
    """
    if rows:
        models = {r.model for r in rows}
        model_str = next(iter(models)) if len(models) == 1 else "(mixed)"
    else:
        model_str = ""

    role_style = _ROLE_STYLE.get(role, "white")
    title = f"[{role_style}]{role}[/{role_style}]"
    if model_str:
        title += rf"  \[{model_str}]"

    if not rows:
        body: RenderableType = "(idle)"
    else:
        body = Group(*[_agent_card(r) for r in rows])

    return rich.panel.Panel(body, title=title)


def render(state: RunState) -> RenderableType:
    """Build a three-column Rich layout from *state*.

    The layout has:
    - A header row: loop counter, backend, dev-ahead, Telegram status.
    - Three columns: Planner | Coder | QA.
    """
    max_loops_str = str(state.max_loops) if state.max_loops > 0 else "∞"
    tg_str = "✓" if state.telegram_ok else "✗"
    header = (
        f"loop {state.current_loop}/{max_loops_str}  "
        f"dev+{state.dev_ahead}  "
        f"backend={state.backend}  "
        f"telegram={tg_str}"
    )

    planners = [r for r in state.agents if r.role == "planner"]
    coders = [r for r in state.agents if r.role == "coder"]
    qa_agents = [r for r in state.agents if r.role == "qa"]

    outer = rich.table.Table.grid(expand=True)
    outer.add_column(ratio=1)
    outer.add_column(ratio=1)
    outer.add_column(ratio=1)

    columns_row = (
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
    wrapper.add_row(outer)
    outer.add_row(*columns_row)

    return wrapper


def live_context(
    renderable: RenderableType | None = None,
    refresh_per_second: int = 4,
) -> rich.live.Live:
    """Return a pre-configured :class:`rich.live.Live` instance.

    Pass *renderable* to set the initial display and avoid a blank first frame.
    """
    return rich.live.Live(renderable, refresh_per_second=refresh_per_second)
