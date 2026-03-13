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
import typer

import orc.config as _cfg
from orc.engine.pool import AgentPool, AgentProcess
from orc.engine.services import (
    AgentService,
    BoardService,
    MessagingService,
    WorkflowService,
    WorktreeService,
)
from orc.messaging import telegram as tg
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
        self._merge_queue: list[str] = []
        self._total_spawned = 0
        self._loop_count: int = 0
        # Soft-block tracking: when a planner is dispatched to resolve one
        # we record (blocked_agent_id, blocked_state) so we can post [orc](resolved).
        self._resolving_soft_block: tuple[str, str] | None = None

        # Graceful shutdown: kill agents on SIGTERM/SIGINT.
        signal.signal(signal.SIGTERM, self._shutdown_handler)
        signal.signal(signal.SIGINT, self._shutdown_handler)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def _set_orc_status(self, status: str, task: str | None = None) -> None:
        """Update the orchestrator card via the optional callback."""
        if self.hooks.on_orc_status is not None:
            self.hooks.on_orc_status(status, task)

    def _echo(self, msg: str) -> None:
        """Write *msg* to stdout only when the TUI is not active.

        When ``on_orc_status`` is wired (TUI mode) the Textual app owns the
        terminal; echoing to stdout produces invisible or garbled output.
        In that case the orc card already surfaces the relevant status, so
        the echo is simply skipped.
        """
        if self.hooks.on_orc_status is None:
            typer.echo(msg)

    @property
    def total_agent_calls(self) -> int:
        """Total number of agent sessions spawned so far."""
        return self._total_spawned

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

    def _loop(self, maxcalls: int) -> None:
        while True:
            self._loop_count += 1
            import structlog.contextvars as _cv

            _cv.clear_contextvars()
            _cv.bind_contextvars(cycle=self._loop_count)
            self._set_orc_status("running", "checking pending work")
            messages = self.messaging.get_messages()

            # 1. Poll for completed agents.
            if not self.pool.is_empty():
                self._set_orc_status("running", "polling completed agents")
            for agent, rc in self.pool.poll():
                self._handle_completion(agent, rc, messages)
                # Refresh messages after completion (agent may have posted).
                messages = self.messaging.get_messages()

            # 2. Drain merge queue (serialized; one merge per cycle).
            if self._merge_queue:
                task = self._merge_queue.pop(0)
                self._do_merge(task)
                messages = self.messaging.get_messages()

            # 3. Watchdog: kill stuck agents.
            timeout_sec = self.squad.timeout_minutes * 60.0
            for agent in self.pool.check_watchdog(timeout_sec):
                self._handle_watchdog(agent)

            # 4. Check for hard-blocked state (pauses all new dispatches).
            blocked_agent, blocked_state = self.messaging.has_unresolved_block(messages)
            if blocked_agent and blocked_state == "blocked":
                self._handle_hard_block(blocked_agent, messages)
                messages = self.messaging.get_messages()

            # 5. Dispatch new agents (skip when the call limit is already reached).
            at_limit = self._total_spawned >= maxcalls
            if not at_limit:
                if not self.dry_run or self._total_spawned == 0:
                    call_budget = maxcalls - self._total_spawned
                    dispatched = self._dispatch(messages, call_budget=call_budget)
                    self._total_spawned += dispatched
                else:
                    dispatched = 0  # pragma: no cover
            else:
                dispatched = 0

            # 6. Check termination.
            if self.dry_run:
                logger.info("dry-run mode: printed one cycle, stopping")
                break

            # When the call limit is reached, keep polling until all running
            # agents finish, then stop.  This avoids orphaning agents that were
            # already in-flight when the limit was hit.
            if at_limit and self.pool.is_empty():
                self._set_orc_status("shutting down")
                logger.info("reached maxcalls and pool drained, stopping", maxcalls=maxcalls)
                self._echo(f"\n↩ Reached --maxcalls {maxcalls}. Stopping.")
                break

            # Check idle-complete: nothing running, nothing to dispatch.
            if not at_limit and self.pool.is_empty() and dispatched == 0:
                self._set_orc_status("running", "checking pending work")
                # When only_role is set, we can't rely on has_pending_work
                # because it checks all roles.  If nothing was dispatched for
                # the filtered role, the workflow is done for that role.
                if self.only_role is not None or not Dispatcher.has_pending_work(
                    self.board, self.messaging, messages
                ):
                    self._set_orc_status("shutting down")
                    if self.only_role is not None:
                        logger.info(
                            "no dispatchable work for filtered role — stopping",
                            only_role=self.only_role,
                        )
                        self._echo(
                            f"\n✓ No dispatchable work for --agent {self.only_role}. Stopping."
                        )
                    else:
                        logger.info("no pending work and pool empty — workflow complete")
                        self._echo("\n✓ No pending work. Workflow complete.")
                    break

            time.sleep(_POLL_INTERVAL)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, messages: list[dict], call_budget: int) -> int:
        """Spawn up to `call_budget` agents for all unassigned work.

        When ``self.only_role`` is set, only agents matching that role are
        dispatched; merge-queue bookkeeping and board operations (QA_PASSED,
        CLOSE_BOARD) still run so the workflow state stays consistent.

        Return number spawned.
        """
        remaining_budget = call_budget

        def _spawn(call):
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
        open_tasks = self.board.get_open_tasks()

        if not open_tasks:
            # No open tasks — queue any unmerged feat/* branches for merge
            # and/or dispatch a planner for unplanned vision docs.
            # _do_merge handles clean merges automatically; a coder is only
            # spawned if there are conflicts.
            pending_reviews = self.board.get_pending_reviews()
            for branch in pending_reviews:
                # Convert branch name → task name.
                # With a branch prefix (e.g. "orc"), branches look like
                # "orc/feat/NNNN-foo"; without a prefix they are "feat/NNNN-foo".
                # Strip everything up to and including "feat/" to get the stem.
                feat_idx = branch.find("feat/")
                task_stem = branch[feat_idx + len("feat/") :] if feat_idx != -1 else branch
                task_name = task_stem + ".md"
                if task_name not in self._merge_queue:
                    self._merge_queue.append(task_name)
                    dispatched += 1
            if not self.board.get_pending_visions():
                return dispatched
            if _role_allowed(AgentRole.PLANNER) and self.pool.count_by_role(AgentRole.PLANNER) == 0:
                dispatched += self._spawn_planner(messages)
            return dispatched
        else:
            # Keep the pipeline full when open tasks are fewer
            # than the maximum number of coders that can run in parallel.  Without
            # this, all coder slots may sit idle waiting for the last remaining
            # task to finish before a new planner run creates more work.
            if (
                _role_allowed(AgentRole.PLANNER)
                and len(open_tasks) < self.squad.count(AgentRole.CODER)
                and self.board.get_pending_visions()
                and self.pool.count_by_role(AgentRole.PLANNER) == 0
            ):
                dispatched += _spawn(lambda: self._spawn_planner(messages))

        pending_reviews = self.board.get_pending_reviews()
        for branch in pending_reviews:
            # Convert branch name → task name.
            # With a branch prefix (e.g. "orc"), branches look like
            # "orc/feat/NNNN-foo"; without a prefix they are "feat/NNNN-foo".
            # Strip everything up to and including "feat/" to get the stem.
            feat_idx = branch.find("feat/")
            task_stem = branch[feat_idx + len("feat/") :] if feat_idx != -1 else branch
            task_name = task_stem + ".md"

            if task_name not in self._merge_queue:
                self._merge_queue.append(task_name)
                dispatched += 1

        # Check for soft-block: route one planner to resolve it.
        blocked_agent, blocked_state = self.messaging.has_unresolved_block(messages)
        if blocked_agent and blocked_state == "soft-blocked":
            if _role_allowed(AgentRole.PLANNER) and self.pool.count_by_role(AgentRole.PLANNER) == 0:
                self._resolving_soft_block = (blocked_agent, blocked_state)
                agent_id = self._next_id(AgentRole.PLANNER)
                dispatched += _spawn(
                    lambda: self._spawn_agent(AgentRole.PLANNER, agent_id, None, messages)
                )
            return dispatched

        # Dispatch coder/QA for each unassigned task up to squad capacity.
        for task in open_tasks:
            task_name = task["name"] if isinstance(task, dict) else str(task)
            assigned_to = task.get("assigned_to") if isinstance(task, dict) else None

            if assigned_to:
                continue  # already assigned to a running agent

            token, reason = self.workflow.derive_task_state(task_name)
            logger.debug("task state", task=task_name, token=token, reason=reason)

            if token == QA_PASSED:
                if task_name not in self._merge_queue:
                    self._merge_queue.append(task_name)
                continue

            if token == CLOSE_BOARD:
                # FIXME: this could fail! Wrap in a try/except and log.
                #  Do the same for all calls out of _dispatch, this is a critical path.
                self.workflow.do_close_board(task_name)
                continue

            if token not in (AgentRole.CODER, AgentRole.QA):
                continue

            if not _role_allowed(token):
                continue

            # Check squad capacity for this role.
            if self.pool.count_by_role(token) >= self.squad.count(token):
                continue

            agent_id = self._next_id(token)
            dispatched += _spawn(lambda: self._spawn_agent(token, agent_id, task_name, messages))

        return dispatched

    # ------------------------------------------------------------------
    # Agent spawn helpers
    # ------------------------------------------------------------------

    def _next_id(self, role: AgentRole | str) -> str:
        self._id_counters[role] += 1
        return tg.make_agent_id(role, self._id_counters[role])

    def _spawn_planner(self, messages: list[dict]) -> int:
        agent_id = self._next_id(AgentRole.PLANNER)
        self._spawn_agent(AgentRole.PLANNER, agent_id, None, messages)
        return 1

    def _spawn_agent(
        self,
        role: AgentRole | str,
        agent_id: str,
        task_name: str | None,
        messages: list[dict],
    ) -> None:
        """Build context and spawn an agent subprocess (or print for dry-run)."""
        if role == AgentRole.PLANNER:
            worktree = self.worktree.ensure_dev_worktree()
        elif task_name:
            worktree = self.worktree.ensure_feature_worktree(task_name)
        else:
            raise ValueError(f"No worktree: role={role!r} requires task_name")

        model, context = self.agent.build_context(role, agent_id, messages, worktree)

        if self.dry_run:
            typer.echo(f"Would spawn agent '{agent_id}' (model={model}, {len(context)} chars)")
            return

        body = self.messaging.boot_message_body()
        self.messaging.post_boot_message(agent_id, body)

        import structlog.contextvars as _cv

        _cv.bind_contextvars(agent_id=agent_id)

        log_path = _cfg.get().log_dir / "agents" / f"{agent_id}.log"
        spawn_result = self.agent.spawn(context, worktree, model, log_path)

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

    def _handle_completion(self, agent: AgentProcess, rc: int, messages: list[dict]) -> None:
        logger.info("agent exited", agent_id=agent.agent_id, exit_code=rc)
        self.pool.remove(agent.agent_id)
        self.pool.close_log(agent)
        _cleanup_context_tmp(agent.context_tmp)
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

        # Post [orc](resolved) if this planner was resolving a soft-block.
        if agent.role == AgentRole.PLANNER and self._resolving_soft_block is not None:
            blocked_a, blocked_s = self._resolving_soft_block
            self.messaging.post_resolved(blocked_a, blocked_s, agent.agent_id)
            self._resolving_soft_block = None

    # ------------------------------------------------------------------
    # Merge (serialized)
    # ------------------------------------------------------------------

    def _do_merge(self, task_name: str) -> None:
        self._set_orc_status("running", f"merging task {task_name}")
        self._echo(f"\n⟳ Merging {task_name} into dev…")
        try:
            self.workflow.merge_feature(task_name)
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
        _cleanup_context_tmp(agent.context_tmp)
        if agent.task_name:
            self.board.unassign_task(agent.task_name)

    # ------------------------------------------------------------------
    # Hard-block handling
    # ------------------------------------------------------------------

    def _handle_hard_block(self, blocked_agent_id: str, messages: list[dict]) -> None:
        self._set_orc_status("running", f"handling hard-blocked {blocked_agent_id}")
        self._echo(f"\n⏸  {blocked_agent_id}(blocked) — waiting for your reply in Telegram…")
        logger.info("hard block detected, waiting for human reply", agent=blocked_agent_id)
        try:
            human_reply = self.messaging.wait_for_human_reply(messages)
        except TimeoutError:
            timeout_h = self.squad.timeout_minutes / 60.0
            timeout_msg = tg.format_agent_message(
                blocked_agent_id,
                "blocked",
                f"Stopped waiting for human reply after {timeout_h:.0f}h. Exiting.",
            )
            tg.send_message(timeout_msg)
            self._set_orc_status("shutting down", "timed out waiting for human reply")
            self._echo("\n✗ Timed out waiting for human reply. Stopping.")
            raise typer.Exit(code=1)
        self._set_orc_status("running", "resuming after human reply")
        self._echo(f"\n↩ Reply received: {human_reply[:80]!r}. Resuming…")
        # Post [orc](resolved) so _has_unresolved_block returns (None, None) on
        # the next cycle instead of seeing the block as still active.
        self.messaging.post_resolved(blocked_agent_id, "blocked", "human-reply")

    # ------------------------------------------------------------------
    # Idle / done detection
    # ------------------------------------------------------------------

    @staticmethod
    def has_pending_work(
        board: BoardService, messaging: MessagingService, messages: list[dict]
    ) -> bool:
        """Return True if there is any work that *could* be dispatched next cycle.

        Exposed as a public static method so callers (e.g. ``orc run``) can
        perform an early-exit check before entering the dispatch loop.
        """
        if board.get_open_tasks():
            return True
        if board.get_pending_visions() or board.get_pending_reviews():
            return True
        # Check if blocked: hard-blocked means work exists but is stalled.
        blocked_agent, _ = messaging.has_unresolved_block(messages)
        return blocked_agent is not None

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
