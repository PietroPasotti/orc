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

from dataclasses import dataclass, field

import rich.live
import rich.table
from rich.console import RenderableType


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


def render(state: RunState) -> RenderableType:
    """Build a :class:`rich.table.Table` from *state*.

    The table has one row per agent in ``state.agents`` plus a footer row
    showing global metadata.
    """
    table = rich.table.Table(
        title="orc run",
        show_header=True,
        header_style="bold",
        expand=True,
    )
    table.add_column("Agent", style="bold")
    table.add_column("Role")
    table.add_column("Model")
    table.add_column("Status")
    table.add_column("Task")
    table.add_column("Worktree")

    for row in state.agents:
        role_style = _ROLE_STYLE.get(row.role, "white")
        table.add_row(
            row.agent_id,
            f"[{role_style}]{row.role}[/{role_style}]",
            row.model,
            row.status,
            row.task_name or "—",
            row.worktree,
        )

    max_loops_str = str(state.max_loops) if state.max_loops > 0 else "∞"
    tg_str = "✓" if state.telegram_ok else "✗"
    footer = (
        f"loop {state.current_loop}/{max_loops_str}  "
        f"dev+{state.dev_ahead}  "
        f"backend={state.backend}  "
        f"telegram={tg_str}"
    )
    table.caption = footer

    return table


def live_context(refresh_per_second: int = 4) -> rich.live.Live:
    """Return a pre-configured :class:`rich.live.Live` instance."""
    return rich.live.Live(refresh_per_second=refresh_per_second)
