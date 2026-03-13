"""Unified work-snapshot for the orc orchestrator.

:class:`Work` is a frozen dataclass that captures all dispatchable work
in a single snapshot.  Both the dispatcher loop and ``orc status`` build
one instance at the start of each cycle/invocation and read from it,
avoiding repeated queries to board, git, and the file-system.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Work:
    """Immutable snapshot of all pending work in the orchestrator.

    Attributes
    ----------
    open_tasks:
        Open task dicts from ``board.yaml`` (same shape as
        :func:`orc.board.get_open_tasks`).
    open_visions:
        Vision ``.md`` filenames that have no matching board task yet.
    open_todos_and_fixmes:
        ``{"file", "line", "tag", "text"}`` dicts for every ``#TODO`` /
        ``#FIXME`` found in the repository source (from ``git grep``).
    open_PRs:
        ``feat/*`` branch names that are not yet merged into dev.
    stalled_agents:
        List of ``(agent_id, state)`` tuples for agents whose last
        Telegram message was ``"blocked"`` or ``"soft-blocked"`` with no
        subsequent ``[orc](resolved)`` reply.  Currently at most one
        entry (the dispatcher resolves blocks serially), but the list
        representation is future-proof.
    """

    open_tasks: list[dict]
    open_visions: list[str]
    open_todos_and_fixmes: list[dict]
    open_PRs: list[str]
    stalled_agents: list[tuple[str, str]]

    # ------------------------------------------------------------------
    # Aggregate queries
    # ------------------------------------------------------------------

    def any_work(self) -> bool:
        """Return ``True`` if there is any work that could be dispatched."""
        return bool(
            self.open_tasks
            or self.open_visions
            or self.open_todos_and_fixmes
            or self.open_PRs
            or self.stalled_agents
        )

    @property
    def has_planner_work(self) -> bool:
        """Return ``True`` if the planner has anything to plan.

        The planner's job is to convert vision docs and TODO/FIXME
        comments into board tasks.  Open tasks that already exist on the
        board are *not* planner work.
        """
        return bool(self.open_visions or self.open_todos_and_fixmes)

    @property
    def hard_blocked(self) -> tuple[str, str] | None:
        """Return the first ``(agent_id, "blocked")`` entry, or ``None``."""
        for agent_id, state in self.stalled_agents:
            if state == "blocked":
                return (agent_id, state)
        return None

    @property
    def soft_blocked(self) -> tuple[str, str] | None:
        """Return the first ``(agent_id, "soft-blocked")`` entry, or ``None``."""
        for agent_id, state in self.stalled_agents:
            if state == "soft-blocked":
                return (agent_id, state)
        return None
