"""Explicit workflow state machine for the orc orchestrator.

This module provides two complementary views of the orc state machine:

1. **Formal model** (:class:`WorldState` + :func:`route` + :func:`successors`)

   A complete, pure-Python encoding of the orchestrator's routing logic.
   ``WorldState`` captures every input that influences the next-agent decision
   (board, git, Telegram).  ``route`` maps a ``WorldState`` to the next action
   without any I/O.  ``successors`` enumerates every possible next
   ``WorldState`` after an action completes (nondeterministic agent outcomes).

   The per-task git fields are also encapsulated in :class:`TaskState`, which
   is reused by the system-level multi-task model.

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

The deadlock-freedom proof covers **one task at a time**.  The system-level
multi-task model (``SystemState`` / ``system_route`` / ``system_successors``)
extends this guarantee to N concurrent tasks; see below.

Architecture note
-----------------
``route()`` is the **authoritative** routing function.
:func:`orc.git.core._derive_task_state` delegates to ``route()`` after
collecting live git state, ensuring there is a single source of truth.
Cross-check tests in ``tests/test_state_machine.py`` verify consistency.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path  # noqa: F401 — kept for public API compatibility

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
    CODER_DONE = "coder_done"  # structured exit: chore(<id>.done.<code>): …
    QA_PASSED = "qa_passed"  # structured exit approve, or legacy "qa(passed):"
    QA_OTHER = "qa_other"  # structured exit reject, or legacy "qa(" (not passed)


class BlockState(Enum):
    """Unresolved Telegram block state."""

    NONE = "none"
    SOFT = "soft"  # soft-blocked → route to planner
    HARD = "hard"  # blocked → stop, wait for human


@dataclass(frozen=True)
class TaskState:
    """Per-task git snapshot used by both :class:`WorldState` and the system model.

    Invariants
    ----------
    * ``commits_ahead=True`` implies ``branch_exists=True``.
    * ``last_commit`` is meaningful only when ``commits_ahead=True``; use
      :data:`LastCommit.NONE` otherwise.
    * ``merged_into_dev`` is only populated when the branch still exists
      (i.e. ``branch_exists=True``); for a missing branch use the default
      ``False`` and dispatch a coder.
    """

    branch_exists: bool = False
    """Feature branch (``feat/XXXX``) exists locally."""

    commits_ahead: bool = False
    """Branch has at least one commit not in main."""

    merged_into_dev: bool = False
    """Branch tip is an ancestor of dev (already merged, branch still present)."""

    last_commit: LastCommit = LastCommit.NONE
    """Subject line category of the most recent commit on the feature branch."""


@dataclass(frozen=True)
class WorldState:
    """Complete snapshot of every input that drives the orchestrator routing.

    This is a pure-data, I/O-free representation.  One :class:`WorldState`
    corresponds to exactly one ``route()`` decision; ``successors()`` maps it
    to the set of possible next states after an agent run completes.

    The per-task git fields mirror :class:`TaskState` and are only consulted
    when ``has_open_task=True``.
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
    """Branch tip is an ancestor of dev (already merged, branch still present)."""

    last_commit: LastCommit = LastCommit.NONE
    """Subject line category of the most recent commit on the feature branch."""

    # --- telegram -------------------------------------------------------------
    block: BlockState = BlockState.NONE
    """Most recent unresolved block message in the chat."""

    def task_state(self) -> TaskState:
        """Return the per-task git fields as a :class:`TaskState`."""
        return TaskState(
            branch_exists=self.branch_exists,
            commits_ahead=self.commits_ahead,
            merged_into_dev=self.merged_into_dev,
            last_commit=self.last_commit,
        )


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

    :func:`orc.git.core._derive_task_state` delegates its routing decision to
    this function, so ``route()`` is the single source of truth.
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
    if not state.branch_exists:
        # Branch was never created (or was cleaned up after a proper merge that
        # already closed the board — so if it's still on the board it needs a
        # coder to create it).
        return "coder"

    if not state.commits_ahead:
        # Branch exists but has no commits ahead of main.
        # This happens when the branch was re-created after a merge (but board
        # not yet updated) or when the coder hasn't committed anything yet.
        if state.merged_into_dev:
            return ACTION_CLOSE_BOARD
        return "coder"

    # Branch has commits ahead of main.
    if state.last_commit == LastCommit.QA_PASSED:
        return ACTION_QA_PASSED
    if state.last_commit == LastCommit.QA_OTHER:
        return "coder"

    # Coder explicitly signalled done via close_task.sh → send to QA.
    if state.last_commit == LastCommit.CODER_DONE:
        return "qa"

    # Ordinary coder commit (CODER_WORK or unknown) — coder is still working.
    return "coder"


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
                # Coder makes ordinary commits — branch now has work ahead of main.
                replace(
                    state,
                    branch_exists=True,
                    commits_ahead=True,
                    merged_into_dev=False,
                    last_commit=LastCommit.CODER_WORK,
                    block=BlockState.NONE,
                ),
                # Coder signals done via structured exit commit.
                replace(
                    state,
                    branch_exists=True,
                    commits_ahead=True,
                    merged_into_dev=False,
                    last_commit=LastCommit.CODER_DONE,
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
# System-level model — SystemState, system_route(), system_successors()
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SystemState:
    """Full system state for N concurrent tasks.

    The per-task list lives in ``tasks`` as a frozenset of
    :class:`TaskState` objects (each one an open, not-yet-merged task).  The
    shared fields ``pending_visions`` and ``block`` mirror the same concepts
    in :class:`WorldState`.

    Parameters
    ----------
    tasks:
        The set of currently open tasks (unordered; identity comes from their
        git state, not a task name).  Empty when all tasks are done.
    pending_visions:
        Number of unprocessed vision documents capped at 2 (0 / 1 / 2+).
        Capping at 2 keeps the state space finite without losing any property.
    block:
        System-wide block state derived from the Telegram scan.  A
        :attr:`BlockState.SOFT` pauses dispatch for all tasks (matches the
        dispatcher's current behaviour).
    """

    tasks: frozenset[TaskState] = field(default_factory=frozenset)
    pending_visions: int = 0
    block: BlockState = BlockState.NONE

    def __post_init__(self) -> None:
        object.__setattr__(self, "pending_visions", min(self.pending_visions, 2))


def _system_is_complete(state: SystemState) -> bool:
    """Return True when the system has no more work to do."""
    return not state.tasks and state.pending_visions == 0 and state.block != BlockState.HARD


def system_route(state: SystemState) -> dict[TaskState, str] | None:
    """Return the set of dispatch actions for *state* (pure, no I/O).

    Returns
    -------
    dict[TaskState, str]
        Maps each task to the action (agent name or sentinel) it should receive.
        An empty dict means the system is complete.
    None
        The system is hard-blocked; no actions should be taken.

    The soft-block semantics mirror the dispatcher: when the system is
    soft-blocked, a single ``"planner"`` action is returned for a synthetic
    (no-task) entry and all per-task dispatch is suppressed.

    Interleaving semantics
    ----------------------
    Unlike :func:`route`, which models a single task, this function returns
    *all* eligible per-task actions simultaneously.  :func:`system_successors`
    then applies *one* action at a time (interleaving semantics) to avoid the
    Cartesian product explosion.
    """
    if state.block == BlockState.HARD:
        return None

    if state.block == BlockState.SOFT:
        # Planner needed to resolve block; no per-task dispatch.
        return {"__soft_block_planner__": "planner"}  # type: ignore[dict-item]

    actions: dict[TaskState, str] = {}

    for task in state.tasks:
        w = WorldState(
            has_open_task=True,
            branch_exists=task.branch_exists,
            commits_ahead=task.commits_ahead,
            merged_into_dev=task.merged_into_dev,
            last_commit=task.last_commit,
        )
        action = route(w)
        if action is not None:
            actions[task] = action

    if not actions and not state.tasks:
        if state.pending_visions > 0:
            return {"__vision_planner__": "planner"}  # type: ignore[dict-item]
        return {}  # COMPLETE

    return actions


def system_successors(state: SystemState) -> frozenset[SystemState]:
    """Return every :class:`SystemState` reachable from *state* in one step.

    Uses **interleaving semantics**: exactly one task's action completes per
    step.  This avoids the Cartesian product explosion while still exploring
    all interleavings of concurrent agents.

    The outcomes for each action mirror :func:`successors` for the single-task
    model.
    """
    actions = system_route(state)

    if actions is None:
        return frozenset()  # hard-blocked terminal

    if not actions:
        return frozenset()  # COMPLETE terminal

    result: set[SystemState] = set()

    # Soft-block planner action.
    if "__soft_block_planner__" in actions:
        result.add(
            SystemState(
                tasks=state.tasks,
                pending_visions=state.pending_visions,
                block=BlockState.NONE,
            )
        )
        return frozenset(result)

    # Vision planner action.
    if "__vision_planner__" in actions:
        new_task = TaskState()
        result.add(
            SystemState(
                tasks=frozenset({new_task}),
                pending_visions=max(0, state.pending_visions - 1),
                block=BlockState.NONE,
            )
        )
        return frozenset(result)

    # Per-task actions — pick ONE task per step (interleaving).
    for task, action in actions.items():
        if action == ACTION_CLOSE_BOARD or action == ACTION_QA_PASSED:
            # Deterministic orchestrator action: remove task from the set.
            new_tasks = frozenset(t for t in state.tasks if t != task)
            result.add(
                SystemState(
                    tasks=new_tasks,
                    pending_visions=state.pending_visions,
                    block=state.block,
                )
            )

        elif action == "coder":
            done_task = TaskState(
                branch_exists=True,
                commits_ahead=True,
                merged_into_dev=False,
                last_commit=LastCommit.CODER_WORK,
            )
            done_task_exit = TaskState(
                branch_exists=True,
                commits_ahead=True,
                merged_into_dev=False,
                last_commit=LastCommit.CODER_DONE,
            )
            # Success: coder makes ordinary commits.
            result.add(
                SystemState(
                    tasks=frozenset((state.tasks - {task}) | {done_task}),
                    pending_visions=state.pending_visions,
                    block=state.block,
                )
            )
            # Success: coder signals done via structured exit commit.
            result.add(
                SystemState(
                    tasks=frozenset((state.tasks - {task}) | {done_task_exit}),
                    pending_visions=state.pending_visions,
                    block=state.block,
                )
            )
            # Hard block.
            result.add(
                SystemState(
                    tasks=state.tasks,
                    pending_visions=state.pending_visions,
                    block=BlockState.HARD,
                )
            )
            # Soft block.
            result.add(
                SystemState(
                    tasks=state.tasks,
                    pending_visions=state.pending_visions,
                    block=BlockState.SOFT,
                )
            )

        elif action == "qa":
            qa_passed = TaskState(
                branch_exists=task.branch_exists,
                commits_ahead=task.commits_ahead,
                merged_into_dev=task.merged_into_dev,
                last_commit=LastCommit.QA_PASSED,
            )
            qa_other = TaskState(
                branch_exists=task.branch_exists,
                commits_ahead=task.commits_ahead,
                merged_into_dev=task.merged_into_dev,
                last_commit=LastCommit.QA_OTHER,
            )
            # QA passes.
            result.add(
                SystemState(
                    tasks=frozenset((state.tasks - {task}) | {qa_passed}),
                    pending_visions=state.pending_visions,
                    block=state.block,
                )
            )
            # QA finds issues.
            result.add(
                SystemState(
                    tasks=frozenset((state.tasks - {task}) | {qa_other}),
                    pending_visions=state.pending_visions,
                    block=state.block,
                )
            )
            # Hard block.
            result.add(
                SystemState(
                    tasks=state.tasks,
                    pending_visions=state.pending_visions,
                    block=BlockState.HARD,
                )
            )

        elif action == "planner":  # pragma: no cover
            # Per-task planner action is unreachable via system_route (block states handled above).
            new_task = TaskState()
            result.add(
                SystemState(
                    tasks=frozenset(state.tasks | {new_task}),
                    pending_visions=max(0, state.pending_visions - 1),
                    block=BlockState.NONE,
                )
            )

    return frozenset(result)


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
        :func:`orc.workflow.determine_next_agent`:
        ``(messages: list[dict]) -> tuple[str | None, str]``.
        Can be replaced with a stub in tests.
    """

    determine_next_agent_fn: Callable[..., str | None] = field(repr=False)

    def current_state(self, messages: list[dict]) -> WorkflowState:
        """Return the :class:`WorkflowState` given the Telegram *messages*.

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
        next_agent, _ = self.determine_next_agent_fn(messages)
        return _agent_to_state(next_agent)

    def next_agent(self, messages: list[dict]) -> str | None:
        """Return the name of the next agent to run, or ``None`` if idle."""
        next_agent, _ = self.determine_next_agent_fn(messages)
        return next_agent


def _agent_to_state(agent: str | None) -> WorkflowState:
    """Map an agent name to the corresponding :class:`WorkflowState`."""
    mapping: dict[str | None, WorkflowState] = {
        None: WorkflowState.IDLE,
        "planner": WorkflowState.PLANNING,
        "coder": WorkflowState.CODING,
        "qa": WorkflowState.REVIEWING,
    }
    return mapping.get(agent, WorkflowState.IDLE)
