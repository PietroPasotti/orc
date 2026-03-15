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

import signal
import sys
import time
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

# Seconds between poll cycles.
_POLL_INTERVAL = 5.0


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
    * ``assignable`` — not blocked and not stuck.
    * ``coder_bound`` — assignable tasks that route to coders (not in-review).
    """
    stuck: list[TaskEntry] = []
    assignable: list[TaskEntry] = []
    coder_bound: list[TaskEntry] = []
    for t in tasks:
        if t.status == TaskStatus.STUCK:
            stuck.append(t)
        elif t.status == TaskStatus.BLOCKED:
            continue  # blocked tasks need planner, not dispatch
        else:
            assignable.append(t)
            if t.status != TaskStatus.IN_REVIEW:
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
        return TaskAction.SKIP
    if token == CLOSE_BOARD:
        return TaskAction.CLOSE_BOARD
    if token in (AgentRole.CODER, AgentRole.QA):
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

    # Planner decision.
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

    # Per-task dispatch.
    running: dict[str, int] = dict(role_counts)  # mutable copy for capacity tracking
    for task in assignable:
        if task.assigned_to:
            continue

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

        # Graceful shutdown: kill agents on SIGTERM/SIGINT.
        signal.signal(signal.SIGTERM, self._shutdown_handler)
        signal.signal(signal.SIGINT, self._shutdown_handler)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def _any_work(self) -> bool:
        """Return True if there is any work that could be dispatched.

        Tasks in ``stuck`` status require human intervention and are not
        dispatchable — they don't count as pending work for the purposes of
        this check, so orc can terminate cleanly when only stuck tasks remain.
        """
        tasks = self.board.get_tasks()
        dispatchable_tasks = [t for t in tasks if t.status != TaskStatus.STUCK]
        if dispatchable_tasks:
            return True
        return bool(self.board.get_pending_visions() or self.board.scan_todos())

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
        """
        try:
            self._loop(maxcalls)
        except _ShutdownSignal:
            logger.info("dispatcher shutting down (signal received)")
            self._kill_all_and_unassign()
            raise typer.Exit(code=130)

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    def _poll_completed_agents(self) -> None:
        """Poll completed agents."""
        if not self.pool.is_empty():
            for agent, rc in self.pool.poll():
                self._handle_completion(agent, rc)

    def _kill_timed_out_agents(self) -> None:
        """Kill stuck agents."""
        timeout_sec = self.squad.timeout_minutes * 60.0
        for agent in self.pool.check_watchdog(timeout_sec):
            self._handle_watchdog(agent)

    def _drain_merge_queue(self) -> None:
        """Merge tasks that have been QA-approved (board status ``done``)."""
        for task_name in self.board.query_tasks(status="done"):
            self._do_merge(task_name)

    def _dispatch_agents(self, call_budget: int) -> int:
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

            # When the call limit is reached, keep polling until all running
            # agents finish, then stop.  This avoids orphaning agents that were
            # already in-flight when the limit was hit.
            if self.pool.is_empty():
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
            role_counts={r: self.pool.count_by_role(r) for r in (AgentRole.CODER, AgentRole.QA)},
            role_limits={r: self.squad.count(r) for r in (AgentRole.CODER, AgentRole.QA)},
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
        if role == AgentRole.PLANNER:
            worktree = self.worktree.ensure_dev_worktree()
        elif task_name:
            worktree = self.worktree.ensure_feature_worktree(task_name)
        else:
            raise ValueError(f"No worktree: role={role!r} requires task_name")

        model, context = self.agent.build_context(role, agent_id, task_name=task_name)

        self._total_spawned += 1

        if self.dry_run:
            typer.echo(f"Would spawn agent '{agent_id}' (model={model}, {len(context)} chars)")
            return

        self.messaging.post_boot_message(agent_id, self.agent.boot_message_body(agent_id))
        contextvars.bind_contextvars(agent_id=agent_id)

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

    def _handle_completion(self, agent: AgentProcess, rc: int) -> None:
        logger.info(
            "agent exited",
            agent_id=agent.agent_id,
            role=agent.role,
            task=agent.task_name,
            exit_code=rc,
        )
        self.pool.remove(agent.agent_id)
        self.pool.close_log(agent)
        _cleanup_agent_temps(agent)
        if self.hooks.on_agent_done is not None:
            self.hooks.on_agent_done(agent, rc)

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
                self.board.unassign_task(agent.task_name)
            return

        self._echo(f"\n✓ {agent.agent_id} completed successfully.")

        if agent.task_name:
            self.board.unassign_task(agent.task_name)

    # ------------------------------------------------------------------
    # Merge (serialized)
    # ------------------------------------------------------------------

    def _do_merge(self, task_name: str) -> None:
        self._echo(f"\n⟳ Merging {task_name} into dev…")
        try:
            self.workflow.merge_feature(task_name)
            self.board.delete_task(task_name)
            logger.info("merge succeeded", task=task_name)
            self._echo(f"✓ {task_name} merged.")
        except Exception as exc:
            logger.error("merge failed", task=task_name, error=str(exc))
            self._echo(f"\n✗ Merge failed for {task_name}: {exc}")

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
        raise _ShutdownSignal(signum)


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
