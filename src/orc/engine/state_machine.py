"""Explicit workflow state machine for the orc orchestrator.

This module provides the formal routing model for the orc state machine:

:class:`WorldState` + :func:`route`
    A complete, pure-Python encoding of the orchestrator's routing logic.
    ``WorldState`` captures every input that influences the next-agent decision
    (board, git, Telegram).  ``route`` maps a ``WorldState`` to the next action
    without any I/O.

``route()`` is the **authoritative** routing function.
:func:`orc.git.core._derive_task_state` delegates to ``route()`` after
collecting live git state, ensuring there is a single source of truth.
Cross-check tests in ``tests/test_state_machine.py`` verify consistency.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from orc.squad import AgentRole

# ---------------------------------------------------------------------------
# Formal model — WorldState, route(), successors()
# ---------------------------------------------------------------------------

# Action sentinels (mirror orc.dispatcher.CLOSE_BOARD / QA_PASSED)
ACTION_CLOSE_BOARD = "__close_board"
ACTION_QA_PASSED = "__qa_passed"


class LastCommit(Enum):
    """What the most recent commit on the feature branch signals."""

    NONE = "none"  # branch has no commits, or no readable message
    CODER_WORK = "coder_work"  # ordinary coder commit (feat/fix/refactor/…)
    CODER_DONE = "coder_done"  # structured exit: chore(<id>.done.<code>): …
    QA_PASSED = "qa_passed"  # structured exit: chore(<id>.approve.<code>): …
    QA_OTHER = "qa_other"  # structured exit: chore(<id>.reject.<code>): …


class BlockState(Enum):
    """Unresolved Telegram block state."""

    NONE = "none"
    SOFT = "soft"  # soft-blocked → route to planner
    HARD = "hard"  # blocked → stop, wait for human


@dataclass(frozen=True)
class WorldState:
    """Complete snapshot of every input that drives the orchestrator routing.

    This is a pure-data, I/O-free representation.  One :class:`WorldState`
    corresponds to exactly one ``route()`` decision.

    The per-task git fields are only consulted when ``has_open_task=True``.
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


def route(state: WorldState) -> str | None:
    """Return the next *action* for *state* (pure, no I/O).

    Returns
    -------
    str
        An agent role (:attr:`~orc.squad.AgentRole.CODER`, :attr:`~orc.squad.AgentRole.QA`,
        :attr:`~orc.squad.AgentRole.PLANNER`) or one of the
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
        return AgentRole.PLANNER

    # No open tasks.
    if not state.has_open_task:
        if state.has_pending_vision:
            return AgentRole.PLANNER
        return None  # COMPLETE

    # Has open task — derive per-task git state (mirrors _derive_task_state).
    if not state.branch_exists:
        # Branch was never created (or was cleaned up after a proper merge that
        # already closed the board — so if it's still on the board it needs a
        # coder to create it).
        return AgentRole.CODER

    if not state.commits_ahead:
        # Branch exists but has no commits ahead of main.
        # This happens when the branch was re-created after a merge (but board
        # not yet updated) or when the coder hasn't committed anything yet.
        if state.merged_into_dev:
            return ACTION_CLOSE_BOARD
        return AgentRole.CODER

    # Branch has commits ahead of main.
    if state.last_commit == LastCommit.QA_PASSED:
        return ACTION_QA_PASSED
    if state.last_commit == LastCommit.QA_OTHER:
        return AgentRole.CODER

    # Coder explicitly signalled done via close_task.py → send to QA.
    if state.last_commit == LastCommit.CODER_DONE:
        return AgentRole.QA

    # Ordinary coder commit (CODER_WORK or unknown) — coder is still working.
    return AgentRole.CODER


def is_terminal(state: WorldState) -> bool:
    """Return True if the workflow has reached a terminal state (nothing to do or hard-blocked)."""
    return route(state) is None
