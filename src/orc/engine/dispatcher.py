"""Parallel agent dispatcher for the orc orchestrator.

The :class:`Dispatcher` replaces the sequential ``while True`` loop in
``.orc/main.py`` with a poll-based parallel scheduler that can run multiple
agents concurrently according to a :class:`~orc.squad.SquadConfig`.

Architecture
------------
The dispatcher owns no domain knowledge about git, board YAML, or context
building.  All domain operations are provided by the caller through five
Protocol-typed services defined in :mod:`orc.engine.services`:

* :class:`~orc.engine.services.BoardService` — kanban board + pending-work queries
* :class:`~orc.engine.services.WorktreeService` — git worktree lifecycle
* :class:`~orc.engine.services.MessagingService` — Telegram messaging
* :class:`~orc.engine.services.WorkflowService` — task-state routing, merges
* :class:`~orc.engine.services.AgentService` — context building + process spawn

Optional TUI lifecycle hooks are provided via :class:`DispatchHooks`.

Sentinel values
~~~~~~~~~~~~~~~
``derive_task_state()`` (on :class:`~orc.engine.services.WorkflowService`) may
return these sentinel strings instead of a role:

``QA_PASSED``
    QA committed a ``qa(passed):`` verdict — the dispatcher queues a merge.
``CLOSE_BOARD``
    Crash-recovery: branch was merged but the board entry was not closed.

Both are defined as module-level constants and imported by ``main.py``.

Lifecycle
---------
1. ``Dispatcher.run()`` starts the poll loop.
2. Each cycle: poll running agents → process completions → drain merge queue
   → check watchdog → refresh Telegram messages → handle blocked states
   → dispatch new agents → sleep.
3. On ``KeyboardInterrupt`` or ``SIGTERM``, all running agents are killed
   and their tasks unassigned before the process exits.

Maxloops
--------
``maxloops`` counts **dispatch cycles**, not individual agent invocations.  One
cycle may spawn a full squad's worth of agents (e.g. one coder + one QA running
in parallel).  ``maxloops=1`` (the default) therefore runs one complete round
before stopping; ``maxloops=0`` means "run until no work remains or the
workflow is hard-blocked".
"""

from __future__ import annotations

import re
import signal
import sys
import time
import typing
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto

import structlog
import structlog.contextvars as contextvars
import typer

import orc.config as _cfg
from orc.coordination.board import TaskStatus
from orc.coordination.models import TaskEntry
from orc.engine.pool import AgentPool, AgentProcess
from orc.engine.services import (
    AgentService,
    BoardService,
    MessagingService,
    WorkflowService,
    WorktreeService,
)
from orc.messaging.messages import make_agent_id as _make_agent_id
from orc.squad import AgentRole, SquadConfig

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Sentinel values returned by derive_task_state
# ---------------------------------------------------------------------------

QA_PASSED = "__qa_passed"
CLOSE_BOARD = "__close_board"

# ---------------------------------------------------------------------------
# Dispatcher lifecycle phase
# ---------------------------------------------------------------------------


class DispatcherPhase(Enum):
    """High-level phase of the dispatcher's lifecycle.

    Used by the TUI and other observers to derive whether the dispatcher
    is actively scheduling agents or gracefully winding down.
    """

    RUNNING = auto()
    """Normal operation — dispatching and polling agents."""

    DRAINING = auto()
    """Drain mode — no new agents will be dispatched; waiting for
    running agents to finish (triggered by first SIGINT/SIGTERM or
    user-initiated drain via the TUI quit modal)."""


# Seconds between poll cycles.
_POLL_INTERVAL = 5.0

# Maximum number of consecutive merge failures before marking a task as stuck.
_MAX_MERGE_RETRIES = 3

# Maximum number of consecutive agent failures before marking a task as stuck.
_MAX_AGENT_RETRIES = 3


def _fmt_elapsed(seconds: float) -> str:
    """Format elapsed seconds as a human-readable string."""
    if seconds >= 60:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    return f"{int(seconds)}s"


# ---------------------------------------------------------------------------
# Board snapshot for noop detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoardSnapshot:
    """Lightweight fingerprint of board state for noop detection.

    Taken before spawning an agent and again after it exits.  If the two
    snapshots are identical and the agent exited with code 0, the agent
    produced no observable effect — a *noop*.
    """

    task_statuses: tuple[tuple[str, str], ...]
    """Sorted ``(task_name, status)`` pairs from the board."""

    pending_visions: tuple[str, ...]
    """Sorted pending-vision filenames."""

    blocked_tasks: tuple[str, ...]
    """Sorted blocked-task names."""

    task_state_token: str | None = None
    """For coder/QA: the ``derive_task_state`` routing token for their task."""


class AgentNoopError(RuntimeError):
    """Raised when one or more agents exit without changing board state."""


def _branch_to_task_name(branch: str) -> str:
    """Reverse-map a feature branch name to its task filename.

    ``feat/0001-foo`` → ``0001-foo.md``
    ``prefix/feat/0001-foo`` → ``0001-foo.md``
    """
    slug = branch.rsplit("feat/", 1)[-1] if "feat/" in branch else branch
    return f"{slug}.md"


# ---------------------------------------------------------------------------
# Pure dispatch planning
# ---------------------------------------------------------------------------


class TaskAction(Enum):
    """What the dispatcher should do with a single task."""

    SPAWN = auto()
    CLOSE_BOARD = auto()
    SKIP = auto()


@dataclass(frozen=True)
class SpawnIntent:
    """Instruction to spawn one agent."""

    role: AgentRole
    task_name: str | None  # None for planner


@dataclass(frozen=True)
class BoardOp:
    """Instruction to perform a board-level operation (crash recovery)."""

    task_name: str


@dataclass(frozen=True)
class DispatchPlan:
    """Pure output of :func:`plan_dispatch` — no side effects attached."""

    spawns: list[SpawnIntent] = field(default_factory=list)
    board_ops: list[BoardOp] = field(default_factory=list)


def classify_tasks(
    tasks: list[TaskEntry],
) -> tuple[list[TaskEntry], list[TaskEntry], list[TaskEntry]]:
    """Partition *tasks* into ``(stuck, assignable, coder_bound)``.

    * ``stuck`` — status is ``stuck``, needs human intervention.
    * ``assignable`` — not blocked, not stuck. Includes ``done`` tasks
      (which are merger-bound).
    * ``coder_bound`` — assignable tasks that route to coders (not in-review,
      not done).

    Tasks with status ``done`` are included in ``assignable`` so the merger
    agent can be dispatched for them.
    """
    stuck: list[TaskEntry] = []
    assignable: list[TaskEntry] = []
    coder_bound: list[TaskEntry] = []
    for t in tasks:
        if t.status == TaskStatus.STUCK:
            stuck.append(t)
        elif t.status == TaskStatus.BLOCKED:
            continue  # blocked → planner
        else:
            assignable.append(t)
            if t.status not in (TaskStatus.IN_REVIEW, TaskStatus.DONE):
                coder_bound.append(t)
    return stuck, assignable, coder_bound


def needs_planner(
    *,
    coder_bound_count: int,
    coder_capacity: int,
    has_planner_work: bool,
    planner_running: bool,
    role_allowed: bool,
    has_assignable_tasks: bool,
) -> bool:
    """Return ``True`` when a planner agent should be dispatched.

    When there are **no** assignable tasks the planner is needed whenever there
    is planner work.  When there **are** assignable tasks the planner is only
    spawned proactively to keep the coder pipeline full (coder-bound tasks
    fewer than coder capacity).
    """
    if planner_running or not role_allowed or not has_planner_work:
        return False
    if not has_assignable_tasks:
        return True
    return coder_bound_count < coder_capacity


def task_action(token: str) -> TaskAction:
    """Map a ``derive_task_state`` *token* to a :class:`TaskAction`."""
    if token == QA_PASSED:
        return TaskAction.SPAWN
    if token == CLOSE_BOARD:
        return TaskAction.CLOSE_BOARD
    if token in (AgentRole.CODER, AgentRole.QA, AgentRole.MERGER):
        return TaskAction.SPAWN
    return TaskAction.SKIP


def plan_dispatch(
    *,
    assignable: list[TaskEntry],
    coder_bound: list[TaskEntry],
    has_planner_work: bool,
    only_role: str | None,
    coder_capacity: int,
    planner_running: bool,
    role_counts: dict[str, int],
    role_limits: dict[str, int],
    derive_task_state: Callable[[str, TaskEntry | None], tuple[str, str]],
) -> DispatchPlan:
    """Build a :class:`DispatchPlan` from the current board/pool snapshot.

    Pure function — all inputs are plain data or callbacks; no I/O.
    """

    def _role_allowed(role: str) -> bool:
        return only_role is None or only_role == role

    spawns: list[SpawnIntent] = []
    board_ops: list[BoardOp] = []

    # Per-task dispatch — merger tasks first (highest priority).
    # We do two passes: first for merger-bound tasks (done/QA_PASSED),
    # then for coder/QA tasks. This ensures mergers are dispatched before
    # other agents when budget is limited.
    running: dict[str, int] = dict(role_counts)  # mutable copy for capacity tracking

    merger_tasks: list[TaskEntry] = []
    other_tasks: list[TaskEntry] = []
    for task in assignable:
        if task.assigned_to:
            continue
        token, _reason = derive_task_state(task.name, task)
        if token == QA_PASSED:
            merger_tasks.append(task)
        else:
            other_tasks.append(task)

    # Pass 1: merger-bound tasks (highest priority).
    for task in merger_tasks:
        role = AgentRole.MERGER
        if not _role_allowed(role):
            continue
        current = running.get(role, 0)
        limit = role_limits.get(role, 1)
        if current >= limit:
            continue
        running[role] = current + 1
        spawns.append(SpawnIntent(role=role, task_name=task.name))

    # Pass 2: coder/QA tasks.
    for task in other_tasks:
        token, reason = derive_task_state(task.name, task)
        logger.debug("task state", task=task.name, token=token, reason=reason)

        action = task_action(token)

        if action is TaskAction.SKIP:
            continue

        if action is TaskAction.CLOSE_BOARD:
            board_ops.append(BoardOp(task_name=task.name))
            continue

        # action is SPAWN — token is a role string (coder / qa).
        if not _role_allowed(token):
            continue

        current = running.get(token, 0)
        limit = role_limits.get(token, 1)
        if current >= limit:
            continue

        running[token] = current + 1
        spawns.append(SpawnIntent(role=AgentRole(token), task_name=task.name))

    # Planner decision (after merger/coder/QA so priority ordering is respected).
    if needs_planner(
        coder_bound_count=len(coder_bound),
        coder_capacity=coder_capacity,
        has_planner_work=has_planner_work,
        planner_running=planner_running,
        role_allowed=_role_allowed(AgentRole.PLANNER),
        has_assignable_tasks=bool(assignable),
    ):
        logger.debug("planner needed", coder_bound=len(coder_bound), coder_capacity=coder_capacity)
        spawns.append(SpawnIntent(role=AgentRole.PLANNER, task_name=None))

    return DispatchPlan(spawns=spawns, board_ops=board_ops)


# ---------------------------------------------------------------------------
# Optional TUI lifecycle hooks
# ---------------------------------------------------------------------------


@dataclass
class DispatchHooks:
    """Optional lifecycle hooks for the :class:`Dispatcher`.

    These are wired by the TUI layer and default to ``None`` for plain-log runs.
    """

    on_agent_start: Callable[[AgentProcess], None] | None = None
    """Called immediately after a new agent is added to the pool."""

    on_agent_done: Callable[[AgentProcess, int], None] | None = None
    """Called immediately after a completed agent is removed from the pool."""

    on_orc_status: Callable[[str], None] | None = None
    """Called when the orchestrator's current task description changes.
    Signature: ``(task)`` where *task* is a human-readable description of
    the current phase (e.g. ``"dispatching"``), or ``"idle"`` when the
    orchestrator is between phases."""

    on_feature_merged: Callable[[], None] | None = None
    """Called immediately after a feature branch is successfully merged into dev."""

    on_cycle: Callable[[], None] | None = None
    """Called once per dispatch loop iteration, just before the inter-cycle sleep.

    Useful for periodic TUI state refreshes (e.g. refreshing counters that
    require git queries) without blocking the main dispatch loop."""


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class Dispatcher:
    """Poll-based parallel agent scheduler.

    Parameters
    ----------
    squad:
        The squad configuration (agent counts, watchdog timeout).
    board:
        Kanban board and pending-work queries.
    worktree:
        Git worktree lifecycle management.
    messaging:
        Telegram messaging service.
    workflow:
        Task-state routing, feature merges, and crash-recovery.
    agent:
        Context building and agent subprocess spawning.
    hooks:
        Optional TUI lifecycle hooks (default: ``None`` = plain-log mode).
    dry_run:
        When ``True`` the dispatcher prints agent contexts instead of
        spawning subprocesses.
    """

    def __init__(
        self,
        squad: SquadConfig,
        *,
        board: BoardService,
        worktree: WorktreeService,
        messaging: MessagingService,
        workflow: WorkflowService,
        agent: AgentService,
        hooks: DispatchHooks | None = None,
        dry_run: bool = False,
        only_role: str | None = None,
    ) -> None:
        self.squad = squad
        self.board = board
        self.worktree = worktree
        self.messaging = messaging
        self.workflow = workflow
        self.agent = agent
        self.hooks = hooks or DispatchHooks()
        self.dry_run = dry_run
        self.only_role = only_role
        self.pool = AgentPool()
        self._id_counters: dict[str, int] = defaultdict(int)
        self._total_spawned = 0
        self._stuck_notified: set[str] = set()  # task names already notified as stuck
        self._merge_failures: dict[str, int] = {}  # consecutive merge failure count per task
        self._agent_failures: dict[str, int] = {}  # consecutive agent failure count per task
        self._board_snapshots: dict[
            str, BoardSnapshot
        ] = {}  # pre-spawn snapshots for noop detection
        self.phase: DispatcherPhase = DispatcherPhase.RUNNING

        # Graceful shutdown: two-stage signal handler.
        # First signal enters drain mode; second signal force-kills.
        signal.signal(signal.SIGTERM, self._shutdown_handler)
        signal.signal(signal.SIGINT, self._shutdown_handler)

    @property
    def _shutting_down(self) -> bool:
        """Whether the dispatcher is draining (backward-compat helper)."""
        return self.phase is DispatcherPhase.DRAINING

    @_shutting_down.setter
    def _shutting_down(self, value: bool) -> None:
        self.phase = DispatcherPhase.DRAINING if value else DispatcherPhase.RUNNING

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def _any_work(self) -> bool:
        """Return True if there is any work that could be dispatched.

        Tasks in ``stuck`` status require human intervention and are not
        dispatchable — they don't count as pending work for the purposes of
        this check, so orc can terminate cleanly when only stuck tasks remain.

        Tasks in ``done`` status are handled by :meth:`_drain_merge_queue` and
        keep the loop alive so merge retries can occur.

        Unmerged feature branches (pending reviews) are also considered work
        so the loop stays consistent with the initial ``is_empty()`` gate.
        """
        tasks = self.board.get_tasks()
        dispatchable_tasks = [t for t in tasks if t.status != TaskStatus.STUCK]
        if dispatchable_tasks:
            return True
        return bool(
            self.board.get_pending_visions()
            or self.board.scan_todos()
            or self.board.get_pending_reviews()
        )

    def _set_orc_task(self, task: str) -> None:
        """Update the orchestrator card via the optional callback."""
        if self.hooks.on_orc_status is not None:
            self.hooks.on_orc_status(task)

    def _echo(self, msg: str, final: bool = False) -> None:
        """Write *msg* to stdout only when the TUI is not active.

        When ``on_orc_status`` is wired (TUI mode) the Textual app owns the
        terminal; echoing to stdout produces invisible or garbled output.
        In that case the orc card already surfaces the relevant status, so
        the echo is simply skipped.

        Exception for final messages, which are triggered just before the TUI shuts down.
        """
        if final or self.hooks.on_orc_status is None:
            typer.echo(msg)
        else:
            logger.info(msg)

    def run(self, maxcalls: int = sys.maxsize) -> None:
        """Run the dispatch loop.

        *maxcalls* — maximum total agent invocations; must be >= 1.
        Pass ``sys.maxsize`` (the default) for unlimited.
        Multiple agents may be spawned in parallel within a single cycle.
        When the limit is reached no new agents are dispatched, but any
        agents already running are allowed to finish before the loop exits.
        Stops after *maxcalls* agent calls **or** when the pool is empty and
        there is nothing left to dispatch (workflow complete).

        Shutdown is two-stage: the first SIGINT/SIGTERM enters drain mode
        (no new agents dispatched; running agents finish), the second
        force-kills all agents and exits with code 130.
        """
        try:
            self._loop(maxcalls)
        except _ShutdownSignal:
            logger.info("dispatcher force-shutting down (second signal)")
            self._kill_all_and_unassign()
            raise typer.Exit(code=130)
        except AgentNoopError as exc:
            logger.error("aborting: agent noop detected", details=str(exc))
            self._echo(f"\n✗ {exc}", final=True)
            self._kill_all_and_unassign()
            raise typer.Exit(code=1)

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    def _poll_completed_agents(self) -> None:
        """Poll completed agents and abort on noop detection."""
        if self.pool.is_empty():
            return
        noops: list[str] = []
        for agent, rc in self.pool.poll():
            is_noop = self._handle_completion(agent, rc)
            if is_noop:
                noops.append(agent.agent_id)
        if noops:
            raise AgentNoopError(
                f"Agent(s) exited without changing board state: {', '.join(noops)}. "
                f"Aborting dispatch loop."
            )

    def _kill_timed_out_agents(self) -> None:
        """Kill stuck agents."""
        timeout_sec = self.squad.timeout_minutes * 60.0
        for agent in self.pool.check_watchdog(timeout_sec):
            self._handle_watchdog(agent)

    def _drain_merge_queue(self) -> None:
        """Handle crash-recovery for tasks already merged but not cleaned up.

        Real merging is now delegated to merger agents dispatched by
        :meth:`plan_dispatch`.  This method only handles two edge cases:

        1. Board-tracked ``done`` tasks whose branch was already merged into
           dev (crash between merge and board cleanup) — these are cleaned up.
        2. Orphaned feature branches with no board entry — merged directly
           (these are rare, usually from a crash between branch creation and
           board write).
        """
        # 1. Crash-recovery: board entries for already-merged tasks.
        board_task_names = {t.name for t in self.board.get_tasks()}
        for task_name in self.board.query_tasks(status="done"):
            token, _reason = self.workflow.derive_task_state(task_name)
            if token == CLOSE_BOARD:
                logger.info(
                    "task already merged into dev — cleaning up board",
                    task=task_name,
                )
                self._echo(f"✓ {task_name} already merged — cleaning up board entry.")
                self.board.delete_task(task_name)
                self._merge_failures.pop(task_name, None)

        # 2. Orphaned branches — unmerged feature branches with no board entry.
        for branch in self.board.get_pending_reviews():
            task_name = _branch_to_task_name(branch)
            if task_name in board_task_names:
                continue  # tracked by board — handled by merger agent dispatch
            logger.info("orphaned feature branch detected — merging", branch=branch, task=task_name)
            self._echo(f"\n⟳ Merging orphaned branch {branch} into dev…")
            self._do_merge(task_name)

    def _dispatch_agents(self, call_budget: int) -> int:
        if self.phase is DispatcherPhase.DRAINING:
            logger.debug("dispatch skipped: draining")
            return 0
        if call_budget > 0:
            self._echo("dispatching agents...")
            return self._dispatch(call_budget=call_budget)
        logger.debug("dispatch skipped: budget exhausted")
        return 0

    def _loop(self, maxcalls: int) -> None:
        loop_count: int = 0
        spawn_count: int = 0

        while True:
            loop_count += 1
            contextvars.clear_contextvars()
            contextvars.bind_contextvars(cycle=loop_count)
            logger.debug("dispatch cycle starting", pool_size=len(self.pool.all_agents()))

            self._set_orc_task("polling agents")
            self._poll_completed_agents()

            self._set_orc_task("merging done tasks")
            self._drain_merge_queue()

            self._set_orc_task("checking watchdog")
            self._kill_timed_out_agents()

            # Dispatch new agents (skip when the call limit is already reached).
            self._set_orc_task("dispatching")
            dispatched_count = self._dispatch_agents(maxcalls - spawn_count)
            spawn_count += dispatched_count
            logger.debug(
                "dispatch cycle complete",
                dispatched=dispatched_count,
                total_spawned=spawn_count,
                pool_size=len(self.pool.all_agents()),
            )

            # Check termination.
            if self.dry_run:
                logger.info("dry-run mode: printed one cycle, stopping")
                break

            self._set_orc_task("idle")

            # Periodic TUI refresh (counters that require git/board queries).
            if self.hooks.on_cycle is not None:
                self.hooks.on_cycle()

            # When the call limit is reached, keep polling until all running
            # agents finish, then stop.  This avoids orphaning agents that were
            # already in-flight when the limit was hit.
            if self.pool.is_empty():
                if self.phase is DispatcherPhase.DRAINING:
                    logger.info("drain complete, all agents finished")
                    self._echo("\n✓ Drained. All agents finished.", final=True)
                    break

                if spawn_count == maxcalls:
                    logger.info("maxcalls reached, shutting down", maxcalls=maxcalls)
                    self._echo(f"\n↩ Reached --maxcalls {maxcalls}. Shutting down.", final=True)
                    break

                if not dispatched_count:
                    if self.only_role is not None or not self._any_work():
                        logger.info(
                            "no pending work, shutting down",
                            only_role=self.only_role,
                            total_spawned=spawn_count,
                        )
                        if self.only_role is not None:
                            self._echo(
                                f"\n✓ No dispatchable work for --agent"
                                f" {self.only_role}. Shutting down.",
                                final=True,
                            )
                        else:
                            self._echo("\n✓ No pending work. Workflow complete.", final=True)
                        break

            time.sleep(_POLL_INTERVAL)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _notify_stuck_tasks(self, stuck: list[TaskEntry]) -> None:
        """Notify stuck tasks via Telegram (once per task per dispatcher lifetime)."""
        for task in stuck:
            if task.name in self._stuck_notified:
                continue
            self._stuck_notified.add(task.name)
            last_comment = next((c.text for c in reversed(task.comments or [])), None)
            detail = f" — {last_comment}" if last_comment else ""
            logger.warning("task stuck, notifying", task=task.name, detail=last_comment)
            self.messaging.post_boot_message(
                "orc",
                f"Task {task.name!r} is stuck and needs human intervention{detail}",
            )
            self._echo(
                f"\n🔧 Task {task.name!r} is stuck — human intervention needed."
                + (f" {last_comment}" if last_comment else "")
            )

    def _dispatch(self, call_budget: int) -> int:
        """Spawn up to *call_budget* agents for all unassigned work.

        Reads fresh state from board/git services, builds a pure
        :class:`DispatchPlan`, then executes it.  Returns number spawned.
        """
        open_tasks = self.board.get_tasks()
        has_planner_work = bool(
            self.board.get_pending_visions()
            or self.board.scan_todos()
            or self.board.get_blocked_tasks()
        )

        stuck, assignable, coder_bound = classify_tasks(open_tasks)
        logger.debug(
            "dispatch snapshot",
            open_tasks=len(open_tasks),
            stuck=len(stuck),
            assignable=len(assignable),
            coder_bound=len(coder_bound),
            has_planner_work=has_planner_work,
        )
        self._notify_stuck_tasks(stuck)

        plan = plan_dispatch(
            assignable=assignable,
            coder_bound=coder_bound,
            has_planner_work=has_planner_work,
            only_role=self.only_role,
            coder_capacity=self.squad.count(AgentRole.CODER),
            planner_running=self.pool.count_by_role(AgentRole.PLANNER) > 0,
            role_counts={
                r: self.pool.count_by_role(r)
                for r in (AgentRole.CODER, AgentRole.QA, AgentRole.MERGER)
            },
            role_limits={
                r: self.squad.count(r) for r in (AgentRole.CODER, AgentRole.QA, AgentRole.MERGER)
            },
            derive_task_state=self.workflow.derive_task_state,
        )
        logger.debug(
            "dispatch plan built",
            spawns=len(plan.spawns),
            board_ops=len(plan.board_ops),
            budget=call_budget,
        )

        return self._execute_plan(plan, call_budget)

    def _execute_plan(self, plan: DispatchPlan, budget: int) -> int:
        """Execute a :class:`DispatchPlan`, respecting *budget*. Returns spawned count."""
        dispatched = 0

        for op in plan.board_ops:
            try:
                logger.warning("crash recovery: closing board for merged branch", task=op.task_name)
                typer.echo(f"\n⟳ Crash recovery: closing board entry for {op.task_name}…")
                self.board.delete_task(op.task_name)
            except Exception:
                logger.exception("delete_task failed during crash recovery", task=op.task_name)

        for intent in plan.spawns:
            if dispatched >= budget:
                logger.warning(
                    "dispatch budget exhausted, skipping remaining spawns",
                    skipped_role=intent.role,
                    skipped_task=intent.task_name,
                    budget=budget,
                )
                break

            agent_id = self._next_id(intent.role)
            self._spawn_agent(intent.role, agent_id, intent.task_name)
            dispatched += 1

        return dispatched

    # ------------------------------------------------------------------
    # Board snapshot (noop detection)
    # ------------------------------------------------------------------

    def _take_board_snapshot(self, task_name: str | None = None) -> BoardSnapshot:
        """Capture a lightweight fingerprint of the current board state.

        For task-specific agents (coder / QA) the routing token produced by
        ``derive_task_state`` is included so that git-level progress (new
        commits, status changes) is also captured.
        """
        tasks = self.board.get_tasks()
        task_statuses = tuple(sorted((t.name, t.status) for t in tasks))
        pending_visions = tuple(sorted(self.board.get_pending_visions()))
        blocked_tasks = tuple(sorted(self.board.get_blocked_tasks()))

        task_state_token: str | None = None
        if task_name:
            task_data = next((t for t in tasks if t.name == task_name), None)
            token, _ = self.workflow.derive_task_state(task_name, task_data)
            task_state_token = token

        return BoardSnapshot(
            task_statuses=task_statuses,
            pending_visions=pending_visions,
            blocked_tasks=blocked_tasks,
            task_state_token=task_state_token,
        )

    # ------------------------------------------------------------------
    # Agent spawn helpers
    # ------------------------------------------------------------------

    def _next_id(self, role: AgentRole | str) -> str:
        self._id_counters[role] += 1
        return _make_agent_id(role, self._id_counters[role])

    def _spawn_agent(
        self,
        role: AgentRole,
        agent_id: str,
        task_name: str | None,
    ) -> None:
        """Build context and spawn an agent subprocess (or print for dry-run)."""
        contextvars.bind_contextvars(agent_id=agent_id)
        self._total_spawned += 1

        if role in (AgentRole.PLANNER, AgentRole.MERGER):
            worktree = self.worktree.ensure_dev_worktree()
        elif task_name:
            worktree = self.worktree.ensure_feature_worktree(task_name)
        else:
            raise ValueError(f"No worktree: role={role!r} requires task_name")

        model, context = self.agent.build_context(role, agent_id, task_name=task_name)

        if self.dry_run:
            typer.echo(f"Would spawn agent '{agent_id}' (model={model}, {len(context)} chars)")
            return

        # Snapshot board state for noop detection after exit.
        self._board_snapshots[agent_id] = self._take_board_snapshot(task_name)

        self._log_spawn_boot(role, agent_id, task_name)

        log_path = _cfg.get().log_dir / "agents" / f"{agent_id}.log"
        spawn_result = self.agent.spawn(
            context, worktree, model, log_path, agent_id=agent_id, role=role
        )

        agent = AgentProcess(
            agent_id=agent_id,
            role=role,
            model=model,
            task_name=task_name,
            process=spawn_result.process,
            worktree=worktree,
            log_path=log_path,
            log_fh=spawn_result.log_fh,
            context_tmp=spawn_result.context_tmp,
            mcp_config_tmp=spawn_result.mcp_config_tmp,
        )
        self.pool.add(agent)
        if self.hooks.on_agent_start is not None:
            self.hooks.on_agent_start(agent)

        if task_name:
            self.board.assign_task(task_name, agent_id)

        logger.info(
            "spawned agent",
            agent_id=agent_id,
            role=role,
            model=model,
            task=task_name,
            worktree=str(worktree),
            log=str(log_path),
        )
        self._echo(f"\n⟳ Spawned {agent_id} (log: {log_path})")

    # ------------------------------------------------------------------
    # Completion handling
    # ------------------------------------------------------------------

    def _handle_completion(self, agent: AgentProcess, rc: int) -> bool:
        """Process a completed agent.  Returns ``True`` if the run was a noop.

        A *noop* is an agent that exited with code 0 but left the board state
        unchanged — it consumed compute without producing useful work.
        """
        elapsed = time.monotonic() - agent.started_at
        logger.info(
            "agent exited",
            agent_id=agent.agent_id,
            role=agent.role,
            task=agent.task_name,
            exit_code=rc,
            elapsed_s=round(elapsed, 1),
        )
        self.pool.remove(agent.agent_id)
        if self.hooks.on_agent_done is not None:
            self.hooks.on_agent_done(agent, rc)
        try:
            self.pool.close_log(agent)
            _cleanup_agent_temps(agent)
        except Exception:  # pragma: no cover
            logger.warning("agent cleanup failed", agent_id=agent.agent_id, exc_info=True)

        if rc != 0:
            logger.error(
                "agent failed",
                agent_id=agent.agent_id,
                role=agent.role,
                task=agent.task_name,
                exit_code=rc,
                log=str(agent.log_path),
            )
            self._echo(
                f"\n✗ {agent.agent_id} exited with code {rc}. See {agent.log_path} for details."
            )
            if agent.task_name:
                task_name = agent.task_name
                count = self._agent_failures.get(task_name, 0) + 1
                self._agent_failures[task_name] = count
                if count >= _MAX_AGENT_RETRIES:
                    logger.error(
                        "agent retry limit reached — marking task as stuck",
                        task=task_name,
                        attempts=count,
                    )
                    self._echo(f"⚠ {task_name} failed {count} times — marking as stuck.")
                    self.worktree.cleanup_feature_worktree(task_name)
                    self.board.set_task_status(task_name, TaskStatus.STUCK)
                    self._agent_failures.pop(task_name, None)
                    self._board_snapshots.pop(agent.agent_id, None)
                    return False
                else:
                    logger.warning(
                        "agent failed — retrying task",
                        task=task_name,
                        attempt=count,
                        max=_MAX_AGENT_RETRIES,
                    )
                    self.board.unassign_task(task_name)
            self._board_snapshots.pop(agent.agent_id, None)
            return False

        self._echo(f"\n✓ {agent.agent_id} completed in {_fmt_elapsed(elapsed)}.")

        if agent.task_name:
            self.board.unassign_task(agent.task_name)
            self._agent_failures.pop(agent.task_name, None)

        # Noop detection: compare board state before and after.
        before = self._board_snapshots.pop(agent.agent_id, None)
        if before is not None:
            after = self._take_board_snapshot(agent.task_name)
            if before == after:
                # Planners legitimately find no work (e.g. after a merge
                # cycle empties the board).  Treat as normal completion.
                if agent.role == AgentRole.PLANNER:
                    logger.info(
                        "planner found no work — not a noop",
                        agent_id=agent.agent_id,
                    )
                    return False

                logger.error(
                    "agent noop detected — board state unchanged",
                    agent_id=agent.agent_id,
                    role=agent.role,
                    task=agent.task_name,
                    log=str(agent.log_path),
                )
                self._echo(
                    f"\n✗ {agent.agent_id} exited without changing board state (noop)."
                    f" See {agent.log_path} for details."
                )
                return True

        return False

    # ------------------------------------------------------------------
    # Merge (serialized)
    # ------------------------------------------------------------------

    def _do_merge(self, task_name: str) -> None:
        self._echo(f"\n⟳ Merging {task_name} into dev…")
        try:
            self.workflow.merge_feature(task_name)
            self.board.delete_task(task_name)
            self._merge_failures.pop(task_name, None)
            logger.info("merge succeeded", task=task_name)
            self._echo(f"✓ {task_name} merged.")
            if self.hooks.on_feature_merged is not None:
                self.hooks.on_feature_merged()
        except Exception as exc:
            count = self._merge_failures.get(task_name, 0) + 1
            self._merge_failures[task_name] = count
            logger.error("merge failed", task=task_name, error=str(exc), attempt=count)
            self._echo(f"\n✗ Merge failed for {task_name}: {exc}")
            if count >= _MAX_MERGE_RETRIES:
                logger.warning(
                    "merge retry limit reached — marking task as stuck",
                    task=task_name,
                    attempts=count,
                )
                self._echo(f"⚠ {task_name} failed to merge {count} times — marking as stuck.")
                self.board.set_task_status(task_name, TaskStatus.STUCK)
                self.worktree.cleanup_feature_worktree(task_name)
                self._merge_failures.pop(task_name, None)

    # ------------------------------------------------------------------
    # Watchdog
    # ------------------------------------------------------------------

    def _handle_watchdog(self, agent: AgentProcess) -> None:
        elapsed_min = (time.monotonic() - agent.started_at) / 60
        logger.warning(
            "agent exceeded watchdog timeout",
            agent_id=agent.agent_id,
            role=agent.role,
            task=agent.task_name,
            elapsed_minutes=f"{elapsed_min:.1f}",
            timeout_minutes=self.squad.timeout_minutes,
        )
        self._echo(
            f"\n⚠ {agent.agent_id} exceeded watchdog timeout "
            f"({elapsed_min:.0f} min > {self.squad.timeout_minutes} min). Killing."
        )
        self.pool.kill(agent.agent_id)
        self.pool.remove(agent.agent_id)
        _cleanup_agent_temps(agent)
        if self.hooks.on_agent_done is not None:
            self.hooks.on_agent_done(agent, -1)
        if agent.task_name:
            self.board.unassign_task(agent.task_name)

    # ------------------------------------------------------------------
    # Graceful shutdown
    # ------------------------------------------------------------------

    def _kill_all_and_unassign(self) -> None:
        agents = self.pool.all_agents()
        if agents:
            logger.info(
                "graceful shutdown: killing all agents",
                count=len(agents),
                agent_ids=[a.agent_id for a in agents],
            )
        for agent in agents:
            if agent.task_name:
                self.board.unassign_task(agent.task_name)
        self.pool.kill_all()

    def _shutdown_handler(self, signum: int, _frame: object) -> None:
        if self.phase is not DispatcherPhase.DRAINING:
            self.phase = DispatcherPhase.DRAINING
            logger.info("drain mode activated (first signal)", signum=signum)
        else:
            raise _ShutdownSignal(signum)

    def _log_spawn_boot(self, role: AgentRole, agent_id: str, task_name: str):
        match role:
            case AgentRole.PLANNER:
                out = ""
                if visions := self.board.get_pending_visions():
                    out += "refining vision docs: " + ", ".join(f"`{v}`" for v in visions) + ". "
                else:
                    # We should be more specific here. We know what the planner is doing after all.
                    out += "no pending visions. Refining TODOs and READMEs/unblocking agents."
            case AgentRole.CODER:
                if task_name:
                    self.messaging.post_boot_message(agent_id, f"picking up work/{task_name}.")
            case AgentRole.QA:
                if task_name:
                    task_stem = re.sub(r"\.md$", "", task_name)
                    self.messaging.post_boot_message(agent_id, f"reviewing feat/{task_stem}.")
            case AgentRole.MERGER:
                if task_name:
                    task_stem = re.sub(r"\.md$", "", task_name)
                    self.messaging.post_boot_message(agent_id, f"merging feat/{task_stem}.")
            case _:
                typing.assert_never(role)


class _ShutdownSignal(BaseException):
    """Raised by the signal handler to trigger graceful shutdown."""


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _cleanup_context_tmp(context_tmp: str | None) -> None:
    """Delete the context temp file *context_tmp* (if present)."""
    if context_tmp:
        from pathlib import Path as _Path

        _Path(context_tmp).unlink(missing_ok=True)


def _cleanup_agent_temps(agent: AgentProcess) -> None:
    """Delete all temporary files created for *agent*."""
    _cleanup_context_tmp(agent.context_tmp)
    _cleanup_context_tmp(agent.mcp_config_tmp)
