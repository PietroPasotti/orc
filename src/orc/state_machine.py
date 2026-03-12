"""Explicit workflow state machine for the orc orchestrator.

This module provides two complementary views of the orc state machine:

1. **Formal model** (:class:`WorldState` + :func:`route` + :func:`successors`)

   A complete, pure-Python encoding of the orchestrator's routing logic.
   ``WorldState`` captures every input that influences the next-agent decision
   (board, git, Telegram).  ``route`` maps a ``WorldState`` to the next action
   without any I/O.  ``successors`` enumerates every possible next
   ``WorldState`` after an action completes (nondeterministic agent outcomes).

   This encoding is the authoritative spec for deadlock-freedom and liveness
   properties, and is tested exhaustively in ``tests/test_state_machine.py``.

2. **Coarse enum / wrapper** (:class:`WorkflowState` + :class:`WorkflowStateMachine`)

   A higher-level vocabulary used by the TUI, metrics, and tests that only
   need to know *which role* is active, not the full git/board detail.

Deadlock freedom
----------------
A *deadlock* is any non-terminal :class:`WorldState` from which
:data:`COMPLETE` is unreachable regardless of agent outcomes.  The property
test ``test_no_deadlocks`` (in ``tests/test_state_machine.py``) verifies this
by exhaustively exploring the reachability graph via BFS.

Architecture note
-----------------
``route()`` mirrors the imperative logic in :func:`orc.git._derive_task_state`
and :func:`orc.workflow.determine_next_agent`.  Keeping them in sync is
enforced by parametrised cross-check tests in ``tests/test_state_machine.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

# ---------------------------------------------------------------------------
# Formal model — WorldState, route(), successors()
# ---------------------------------------------------------------------------

# Action sentinels (mirror orc.dispatcher.CLOSE_BOARD / QA_PASSED)
ACTION_CLOSE_BOARD = "__close_board"
ACTION_QA_PASSED = "__qa_passed"
ACTION_COMPLETE = None  # route() returns None when nothing left to do
ACTION_BLOCKED = None  # route() also returns None when hard-blocked


class LastCommit(Enum):
    """What the most recent commit on the feature branch signals."""

    NONE = "none"  # branch has no commits, or no readable message
    CODER_WORK = "coder_work"  # ordinary coder commit (feat/fix/refactor/…)
    QA_PASSED = "qa_passed"  # starts with "qa(passed):"
    QA_OTHER = "qa_other"  # starts with "qa(" but not passed (failed, blocked…)


class BlockState(Enum):
    """Unresolved Telegram block state."""

    NONE = "none"
    SOFT = "soft"  # soft-blocked → route to planner
    HARD = "hard"  # blocked → stop, wait for human


@dataclass(frozen=True)
class WorldState:
    """Complete snapshot of every input that drives the orchestrator routing.

    This is a pure-data, I/O-free representation.  One :class:`WorldState`
    corresponds to exactly one ``route()`` decision; ``successors()`` maps it
    to the set of possible next states after an agent run completes.

    Invariants
    ----------
    * ``commits_ahead=True`` implies ``branch_exists=True``.
    * ``last_commit`` is meaningful only when ``commits_ahead=True``.
    * ``branch_exists`` / ``commits_ahead`` / ``merged_into_dev`` /
      ``last_commit`` are only consulted when ``has_open_task=True``.
    """

    # --- board ----------------------------------------------------------------
    has_open_task: bool
    """Board has at least one entry in ``open``."""

    has_pending_vision: bool = False
    """There are vision docs not yet distilled into a board task."""

    # --- git (meaningful when has_open_task=True) -----------------------------
    branch_exists: bool = False
    """Feature branch (``feat/XXXX``) exists locally."""

    commits_ahead: bool = False
    """Branch has at least one commit not in main."""

    merged_into_dev: bool = False
    """Branch tip is an ancestor of dev (already merged)."""

    last_commit: LastCommit = LastCommit.NONE
    """Subject line category of the most recent commit on the feature branch."""

    # --- telegram -------------------------------------------------------------
    block: BlockState = BlockState.NONE
    """Most recent unresolved block message in the chat."""


def route(state: WorldState) -> str | None:
    """Return the next *action* for *state* (pure, no I/O).

    Returns
    -------
    str
        An agent role (``"coder"``, ``"qa"``, ``"planner"``) or one of the
        orchestrator sentinels :data:`ACTION_CLOSE_BOARD` /
        :data:`ACTION_QA_PASSED`.
    None
        The workflow is terminal — either complete (nothing left to do) or
        hard-blocked (waiting for a human).

    The logic here mirrors :func:`orc.git._derive_task_state` and
    :func:`orc.workflow.determine_next_agent`.  Any change to those functions
    must be reflected here (and vice-versa) to keep the formal model current.
    """
    # Hard block: stop entirely, wait for human.
    if state.block == BlockState.HARD:
        return None

    # Soft block: route to planner to clarify.
    if state.block == BlockState.SOFT:
        return "planner"

    # No open tasks.
    if not state.has_open_task:
        if state.has_pending_vision:
            return "planner"
        return None  # COMPLETE

    # Has open task — derive per-task git state (mirrors _derive_task_state).
    if not state.branch_exists or not state.commits_ahead:
        if state.merged_into_dev:
            return ACTION_CLOSE_BOARD
        return "coder"

    # Branch has commits ahead of main.
    if state.last_commit == LastCommit.QA_PASSED:
        return ACTION_QA_PASSED
    if state.last_commit == LastCommit.QA_OTHER:
        return "coder"

    return "qa"


def successors(state: WorldState) -> frozenset[WorldState]:
    """Return every :class:`WorldState` reachable from *state* in one step.

    Each element of the returned set represents one possible outcome of the
    action returned by :func:`route`.  Agent outcomes are nondeterministic
    (the LLM may succeed, fail, or become blocked), so the set may contain
    multiple elements.

    Orchestrator actions (``ACTION_CLOSE_BOARD``, ``ACTION_QA_PASSED``) are
    deterministic and produce exactly one successor.
    """
    action = route(state)

    if action is None:
        return frozenset()  # terminal — no successors

    # --- orchestrator actions (deterministic) --------------------------------
    if action == ACTION_CLOSE_BOARD:
        # Board entry removed; branch and worktree cleaned up.
        return frozenset(
            {
                WorldState(
                    has_open_task=False,
                    has_pending_vision=state.has_pending_vision,
                )
            }
        )

    if action == ACTION_QA_PASSED:
        # Orchestrator merges feature branch into dev and closes board entry.
        return frozenset(
            {
                WorldState(
                    has_open_task=False,
                    has_pending_vision=state.has_pending_vision,
                )
            }
        )

    # --- agent actions (nondeterministic) ------------------------------------
    if action == "coder":
        return frozenset(
            {
                # Coder makes commits — branch now has work ahead of main.
                replace(
                    state,
                    branch_exists=True,
                    commits_ahead=True,
                    merged_into_dev=False,
                    last_commit=LastCommit.CODER_WORK,
                    block=BlockState.NONE,
                ),
                # Coder gets hard-blocked (ambiguous spec, missing dependency…).
                replace(state, block=BlockState.HARD),
                # Coder gets soft-blocked (needs planner clarification).
                replace(state, block=BlockState.SOFT),
            }
        )

    if action == "qa":
        return frozenset(
            {
                # QA passes — commits qa(passed): on feature branch.
                replace(
                    state,
                    last_commit=LastCommit.QA_PASSED,
                    block=BlockState.NONE,
                ),
                # QA finds issues — commits qa(failed): or qa(blocked):.
                replace(
                    state,
                    last_commit=LastCommit.QA_OTHER,
                    block=BlockState.NONE,
                ),
                # QA gets hard-blocked (e.g. can't run tests at all).
                replace(state, block=BlockState.HARD),
            }
        )

    if action == "planner":
        if state.block == BlockState.SOFT:
            # Planner resolves soft-block → previous routing resumes.
            return frozenset({replace(state, block=BlockState.NONE)})
        if not state.has_open_task:
            # Planner creates a new task from a vision doc.
            return frozenset(
                {
                    WorldState(
                        has_open_task=True,
                        has_pending_vision=False,
                        branch_exists=False,
                        commits_ahead=False,
                        merged_into_dev=False,
                        last_commit=LastCommit.NONE,
                        block=BlockState.NONE,
                    ),
                }
            )

    return frozenset()  # pragma: no cover — all actions handled above


def is_terminal(state: WorldState) -> bool:
    """Return True if *state* has no successors (workflow has ended)."""
    return route(state) is None


def is_complete(state: WorldState) -> bool:
    """Return True if *state* is a successful terminal (nothing left to do)."""
    return is_terminal(state) and state.block != BlockState.HARD


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
