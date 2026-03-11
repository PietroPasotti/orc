"""Explicit workflow state machine for the orc orchestrator.

This module makes the implicit state machine in :mod:`orc.workflow` and
:mod:`orc.dispatcher` explicit.  Using a proper :class:`WorkflowState` enum
gives us:

* Type safety — callers can never pass a raw string where a state is expected.
* Exhaustiveness checking — ``match`` statements will produce mypy/pyright
  warnings if a new state is added but not handled.
* Self-documenting code — the set of all possible states is visible in one
  place.

Architecture
------------
:func:`orc.workflow.determine_next_agent` contains the transition logic.  This
module does **not** replace it — it provides the vocabulary that both callers
and the existing logic can share.

The :class:`WorkflowStateMachine` wraps :func:`determine_next_agent` with a
clean interface so that :class:`~orc.dispatcher.Dispatcher` (and tests) can
reason about states without knowing the internals of the workflow module.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# ---------------------------------------------------------------------------
# State enum
# ---------------------------------------------------------------------------


class WorkflowState(Enum):
    """Possible states of the orc workflow state machine.

    Descriptions
    ------------
    IDLE
        No active task; the orchestrator is waiting for planner input or a new
        vision to be pushed to the board.
    PLANNING
        A planner agent is running or the board has open planning tasks.
    CODING
        A coder agent is running or there are open coding tasks.
    REVIEWING
        A QA agent is running or there are open review tasks.
    MERGING
        A feature branch is ready to merge into dev.
    BLOCKED
        A hard block waiting for human intervention (e.g. a failed QA cycle
        that a human must resolve).
    SOFT_BLOCKED
        A temporary block (usually waiting for an agent to reply) that will
        auto-resolve without human interaction.
    COMPLETE
        All tasks for the current vision are done.
    """

    IDLE = "idle"
    PLANNING = "planning"
    CODING = "coding"
    REVIEWING = "reviewing"
    MERGING = "merging"
    BLOCKED = "blocked"
    SOFT_BLOCKED = "soft_blocked"
    COMPLETE = "complete"


# ---------------------------------------------------------------------------
# Transition table (for documentation / validation)
# ---------------------------------------------------------------------------

#: Allowed transitions between states.  ``None`` in the target set means the
#: state machine can remain in the same state (self-loops are always allowed).
TRANSITIONS: dict[WorkflowState, frozenset[WorkflowState | None]] = {
    WorkflowState.IDLE: frozenset({WorkflowState.PLANNING}),
    WorkflowState.PLANNING: frozenset({WorkflowState.CODING, WorkflowState.IDLE}),
    WorkflowState.CODING: frozenset(
        {
            WorkflowState.REVIEWING,
            WorkflowState.CODING,
            WorkflowState.BLOCKED,
        }
    ),
    WorkflowState.REVIEWING: frozenset(
        {
            WorkflowState.MERGING,
            WorkflowState.CODING,
            WorkflowState.BLOCKED,
        }
    ),
    WorkflowState.MERGING: frozenset({WorkflowState.COMPLETE, WorkflowState.BLOCKED}),
    WorkflowState.BLOCKED: frozenset(
        {
            WorkflowState.CODING,
            WorkflowState.REVIEWING,
            WorkflowState.IDLE,
        }
    ),
    WorkflowState.SOFT_BLOCKED: frozenset(
        {
            WorkflowState.CODING,
            WorkflowState.REVIEWING,
            WorkflowState.IDLE,
        }
    ),
    WorkflowState.COMPLETE: frozenset({WorkflowState.IDLE}),
}


# ---------------------------------------------------------------------------
# State machine wrapper
# ---------------------------------------------------------------------------


@dataclass
class WorkflowStateMachine:
    """Thin wrapper around :func:`orc.workflow.determine_next_agent`.

    This class does **not** hold state itself — the ground truth lives in the
    board YAML and the git worktree.  It provides a clean, testable interface
    for querying the current state and scheduling the next agent.

    Parameters
    ----------
    determine_next_agent_fn:
        A callable with the same signature as
        :func:`orc.workflow.determine_next_agent`.  Defaults to the real
        implementation; can be replaced with a stub in tests.
    """

    determine_next_agent_fn: Callable[..., str | None] = field(repr=False)

    def current_state(
        self,
        task: dict,
        messages: list[dict],
        worktree: Path | None = None,
    ) -> WorkflowState:
        """Return the :class:`WorkflowState` for *task* given *messages*.

        The state is derived from the next-agent decision:

        ========== ==========================
        Next agent Returned state
        ========== ==========================
        ``None``   :attr:`WorkflowState.IDLE`
        planner    :attr:`WorkflowState.PLANNING`
        coder      :attr:`WorkflowState.CODING`
        qa         :attr:`WorkflowState.REVIEWING`
        ========== ==========================

        Any other value returned by the underlying function is mapped to
        :attr:`WorkflowState.IDLE`.
        """
        next_agent = self.determine_next_agent_fn(task, messages, worktree=worktree)
        return _agent_to_state(next_agent)

    def next_agent(
        self,
        task: dict,
        messages: list[dict],
        worktree: Path | None = None,
    ) -> str | None:
        """Return the name of the next agent to run, or ``None`` if idle."""
        return self.determine_next_agent_fn(task, messages, worktree=worktree)


def _agent_to_state(agent: str | None) -> WorkflowState:
    """Map an agent name to the corresponding :class:`WorkflowState`."""
    mapping: dict[str | None, WorkflowState] = {
        None: WorkflowState.IDLE,
        "planner": WorkflowState.PLANNING,
        "coder": WorkflowState.CODING,
        "qa": WorkflowState.REVIEWING,
    }
    return mapping.get(agent, WorkflowState.IDLE)
