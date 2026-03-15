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
from dataclasses import dataclass

import structlog
import structlog.contextvars as contextvars
import typer

import orc.config as _cfg
from orc.coordination.board import TaskStatus
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

    on_orc_status: Callable[[str, str | None], None] | None = None
    """Called whenever the orchestrator's status changes.
    Signature: ``(status, task)`` where *status* is e.g. ``"running"`` or
    ``"shutting down"`` and *task* is a human-readable description of the
    current decision point (e.g. ``"merging task 0042-foo.md"``), or
    ``None`` when the orchestrator is idle."""


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

    def _set_orc_status(self, status: str, task: str | None = None) -> None:
        """Update the orchestrator card via the optional callback."""
        if self.hooks.on_orc_status is not None:
            self.hooks.on_orc_status(status, task)

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
        pool = self.pool

        if not pool.is_empty():
            self._set_orc_status("running", "polling completed agents")
            for agent, rc in pool.poll():
                self._handle_completion(agent, rc)

    def _kill_timed_out_agents(self) -> None:
        """Kill stuck agents."""
        timeout_sec = self.squad.timeout_minutes * 60.0
        for agent in self.pool.check_watchdog(timeout_sec):
            self._set_orc_status("running", f"killing timed-out {agent.agent_id}")
            self._handle_watchdog(agent)

    def _drain_merge_queue(self) -> None:
        """Merge tasks that have been QA-approved (board status ``done``)."""
        for task_name in self.board.query_tasks(status="done"):
            self._set_orc_status("running", f"merging {task_name}")
            self._do_merge(task_name)

    def _dispatch_agents(self, call_budget: int) -> int:
        if call_budget > 0:
            self._echo("dispatching agents...")
            return self._dispatch(call_budget=call_budget)
        return 0

    def _loop(self, maxcalls: int) -> None:
        loop_count: int = 0
        spawn_count: int = 0

        while True:
            loop_count += 1
            contextvars.clear_contextvars()
            contextvars.bind_contextvars(cycle=loop_count)
            self._set_orc_status("running", "cycle {loop_count}")

            self._poll_completed_agents()
            self._drain_merge_queue()
            self._kill_timed_out_agents()

            # Dispatch new agents (skip when the call limit is already reached).
            dispatched_count = self._dispatch_agents(maxcalls - spawn_count)
            # subtract number of dispatched agents from the allowed budget
            spawn_count += dispatched_count

            # Check termination.
            if self.dry_run:
                logger.info("dry-run mode: printed one cycle, stopping")
                break
            # When the call limit is reached, keep polling until all running
            # agents finish, then stop.  This avoids orphaning agents that were
            # already in-flight when the limit was hit.
            if self.pool.is_empty():
                if spawn_count == maxcalls:
                    self._echo(f"\n↩ Reached --maxcalls {maxcalls}. Shutting down.", final=True)
                    break

                if not dispatched_count:
                    # Check idle-complete: nothing running, nothing to dispatch.
                    self._set_orc_status("running", "checking pending work")
                    # When only_role is set, we can't rely on any_work()
                    # because it checks all roles.  If nothing was dispatched for
                    # the filtered role, the workflow is done for that role.
                    if self.only_role is not None or not self._any_work():
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

    def _dispatch(self, call_budget: int) -> int:
        """Spawn up to `call_budget` agents for all unassigned work.

        Reads directly from board/git services on each call so the data
        is always fresh (no stale snapshot).

        When ``self.only_role`` is set, only agents matching that role are
        dispatched; board operations (QA_PASSED, CLOSE_BOARD) still run so
        the workflow state stays consistent.

        Return number spawned.
        """
        remaining_budget = call_budget

        def _spawn(call: Callable[[], object]) -> int:
            nonlocal remaining_budget

            if remaining_budget > 0:
                remaining_budget -= 1
                call()
                return 1
            else:
                logger.warning("skipped dispatch call: maxcalls reached")
                return 0

        def _role_allowed(role: str) -> bool:
            return self.only_role is None or self.only_role == role

        dispatched = 0

        open_tasks = self.board.get_tasks()
        has_planner_work = bool(
            self.board.get_pending_visions()
            or self.board.scan_todos()
            or self.board.get_blocked_tasks()
        )

        # Stuck tasks need human intervention — no agent can help.
        # Notify via Telegram once per task per dispatcher lifetime.
        stuck_tasks = [t for t in open_tasks if t.status == TaskStatus.STUCK]
        for stuck in stuck_tasks:
            if stuck.name not in self._stuck_notified:
                self._stuck_notified.add(stuck.name)
                last_comment = next((c.text for c in reversed(stuck.comments or [])), None)
                detail = f" — {last_comment}" if last_comment else ""
                self.messaging.post_boot_message(
                    "orc",
                    f"Task {stuck.name!r} is stuck and needs human intervention{detail}",
                )
                self._echo(
                    f"\n🔧 Task {stuck.name!r} is stuck — human intervention needed."
                    + (f" {last_comment}" if last_comment else "")
                )

        # Blocked tasks need planner attention; stuck tasks need human intervention.
        assignable_tasks = [
            t for t in open_tasks if t.status not in (TaskStatus.BLOCKED, TaskStatus.STUCK)
        ]

        if not assignable_tasks:
            if not has_planner_work:
                return dispatched
            if _role_allowed(AgentRole.PLANNER) and self.pool.count_by_role(AgentRole.PLANNER) == 0:
                dispatched += self._spawn_planner()
            return dispatched
        else:
            # Keep the pipeline full when open tasks are fewer
            # than the maximum number of coders that can run in parallel.  Without
            # this, all coder slots may sit idle waiting for the last remaining
            # task to finish before a new planner run creates more work.
            if (
                _role_allowed(AgentRole.PLANNER)
                and len(assignable_tasks) < self.squad.count(AgentRole.CODER)
                and has_planner_work
                and self.pool.count_by_role(AgentRole.PLANNER) == 0
            ):
                dispatched += _spawn(lambda: self._spawn_planner())

        # Dispatch coder/QA for each unassigned non-blocked task up to squad capacity.
        for task in assignable_tasks:
            task_name = task.name
            assigned_to = task.assigned_to

            if assigned_to:
                continue  # already assigned to a running agent

            token, reason = self.workflow.derive_task_state(task_name, task)
            logger.debug("task state", task=task_name, token=token, reason=reason)

            if token == QA_PASSED:
                continue

            if token == CLOSE_BOARD:
                try:
                    logger.warning(
                        "crash recovery: closing board for merged branch", task=task_name
                    )
                    typer.echo(f"\n⟳ Crash recovery: closing board entry for {task_name}…")
                    self.board.delete_task(task_name)
                except Exception:
                    logger.exception("delete_task failed during crash recovery", task=task_name)
                continue

            if token not in (AgentRole.CODER, AgentRole.QA):
                continue

            if not _role_allowed(token):
                continue

            # Check squad capacity for this role.
            if self.pool.count_by_role(token) >= self.squad.count(token):
                continue

            agent_id = self._next_id(token)
            dispatched += _spawn(lambda: self._spawn_agent(AgentRole(token), agent_id, task_name))

        return dispatched

    # ------------------------------------------------------------------
    # Agent spawn helpers
    # ------------------------------------------------------------------

    def _next_id(self, role: AgentRole | str) -> str:
        self._id_counters[role] += 1
        return _make_agent_id(role, self._id_counters[role])

    def _spawn_planner(self) -> int:
        agent_id = self._next_id(AgentRole.PLANNER)
        self._spawn_agent(AgentRole.PLANNER, agent_id, None)
        return 1

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
        self._set_orc_status("running", f"dispatching {agent_id}")

        if task_name:
            self.board.assign_task(task_name, agent_id)

        logger.info(
            "spawned agent",
            agent_id=agent_id,
            task=task_name,
            worktree=str(worktree),
            log=str(log_path),
        )
        self._echo(f"\n⟳ Spawned {agent_id} (log: {log_path})")

    # ------------------------------------------------------------------
    # Completion handling
    # ------------------------------------------------------------------

    def _handle_completion(self, agent: AgentProcess, rc: int) -> None:
        logger.info("agent exited", agent_id=agent.agent_id, exit_code=rc)
        self.pool.remove(agent.agent_id)
        self.pool.close_log(agent)
        _cleanup_agent_temps(agent)
        if self.hooks.on_agent_done is not None:
            self.hooks.on_agent_done(agent, rc)

        if rc != 0:
            logger.error(
                "agent failed", agent_id=agent.agent_id, exit_code=rc, log=str(agent.log_path)
            )
            self._set_orc_status("running", f"{agent.agent_id} failed (rc={rc})")
            self._echo(
                f"\n✗ {agent.agent_id} exited with code {rc}. See {agent.log_path} for details."
            )
            if agent.task_name:
                self.board.unassign_task(agent.task_name)
            return

        self._set_orc_status("running", f"{agent.agent_id} completed")
        self._echo(f"\n✓ {agent.agent_id} completed successfully.")

        if agent.task_name:
            self.board.unassign_task(agent.task_name)

    # ------------------------------------------------------------------
    # Merge (serialized)
    # ------------------------------------------------------------------

    def _do_merge(self, task_name: str) -> None:
        self._set_orc_status("running", f"merging task {task_name}")
        self._echo(f"\n⟳ Merging {task_name} into dev…")
        try:
            self.workflow.merge_feature(task_name)
            self.board.delete_task(task_name)
            self._set_orc_status("running", f"merged {task_name}")
            self._echo(f"✓ {task_name} merged.")
        except Exception as exc:
            logger.error("merge failed", task=task_name, error=str(exc))
            self._set_orc_status("running", f"merge failed: {task_name}")
            self._echo(f"\n✗ Merge failed for {task_name}: {exc}")

    # ------------------------------------------------------------------
    # Watchdog
    # ------------------------------------------------------------------

    def _handle_watchdog(self, agent: AgentProcess) -> None:
        elapsed_min = (time.monotonic() - agent.started_at) / 60
        logger.warning(
            "agent exceeded watchdog timeout",
            agent_id=agent.agent_id,
            elapsed_minutes=f"{elapsed_min:.1f}",
            timeout_minutes=self.squad.timeout_minutes,
        )
        self._set_orc_status("running", f"watchdog killed {agent.agent_id}")
        self._echo(
            f"\n⚠ {agent.agent_id} exceeded watchdog timeout "
            f"({elapsed_min:.0f} min > {self.squad.timeout_minutes} min). Killing."
        )
        self.pool.kill(agent.agent_id)
        self.pool.remove(agent.agent_id)
        _cleanup_agent_temps(agent)
        if agent.task_name:
            self.board.unassign_task(agent.task_name)

    # ------------------------------------------------------------------
    # Graceful shutdown
    # ------------------------------------------------------------------

    def _kill_all_and_unassign(self) -> None:
        for agent in self.pool.all_agents():
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
