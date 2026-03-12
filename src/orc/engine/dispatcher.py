"""Parallel agent dispatcher for the orc orchestrator.

The :class:`Dispatcher` replaces the sequential ``while True`` loop in
``orc/main.py`` with a poll-based parallel scheduler that can run multiple
agents concurrently according to a :class:`~orc.squad.SquadConfig`.

Architecture
------------
The dispatcher owns no domain knowledge about git, board YAML, or context
building.  All domain operations are provided by the caller (``main.py``)
through a :class:`DispatchCallbacks` dataclass.  This keeps the dispatcher
testable in isolation.

Sentinel values
~~~~~~~~~~~~~~~
``derive_task_state()`` may return these sentinel strings instead of a role:

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
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import structlog
import typer

from orc.engine.pool import AGENT_LOG_DIR, AgentPool, AgentProcess
from orc.messaging import telegram as tg
from orc.squad import SquadConfig

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Sentinel values returned by derive_task_state
# ---------------------------------------------------------------------------

QA_PASSED = "__qa_passed"
CLOSE_BOARD = "__close_board"

# Seconds between poll cycles.
_POLL_INTERVAL = 5.0


# ---------------------------------------------------------------------------
# Callbacks protocol
# ---------------------------------------------------------------------------


@dataclass
class DispatchCallbacks:
    """Domain operations provided by ``main.py`` to the :class:`Dispatcher`.

    Every callable is a module-level function (or bound method) from
    ``main.py``.  The dispatcher never imports from ``main`` directly,
    which avoids circular imports.
    """

    # -- Task / board operations -----------------------------------------

    derive_task_state: Callable[[str], tuple[str, str]]
    """Return ``(token, reason)`` for *task_name* where *token* is a role name
    or one of the sentinels :data:`QA_PASSED` / :data:`CLOSE_BOARD`."""

    get_open_tasks: Callable[[], list[dict]]
    """Return the list of open task dicts from board.yaml."""

    assign_task: Callable[[str, str], None]
    """Write ``assigned_to: {agent_id}`` for *task_name* in board.yaml."""

    unassign_task: Callable[[str], None]
    """Clear ``assigned_to`` for *task_name* in board.yaml."""

    # -- Worktree operations ---------------------------------------------

    ensure_feature_worktree: Callable[[str], Path]
    """Ensure the feature worktree for *task_name* exists; return its path."""

    ensure_dev_worktree: Callable[[], Path]
    """Ensure the dev worktree exists; return its path."""

    # -- Merge / close ---------------------------------------------------

    merge_feature: Callable[[str], None]
    """Merge the feature branch for *task_name* into dev and close the board task."""

    do_close_board: Callable[[str], None]
    """Crash-recovery: close the board entry for a task whose branch already merged."""

    # -- Telegram --------------------------------------------------------

    get_messages: Callable[[], list[dict]]
    """Fetch the latest Telegram message history."""

    has_unresolved_block: Callable[[list[dict]], tuple[str | None, str | None]]
    """Return ``(agent_id, state)`` if there is an unresolved block, else
    ``(None, None)``."""

    wait_for_human_reply: Callable[[list[dict]], str]
    """Block until a human replies on Telegram; return the reply text."""

    post_boot_message: Callable[[str, str], None]
    """Send ``[{agent_id}](boot) …`` to Telegram."""

    post_resolved: Callable[[str, str, str], None]
    """Send ``[orc](resolved) …`` to Telegram."""

    boot_message_body: Callable[[], str]
    """Return the body text for a boot message (lists open tasks)."""

    # -- Context building ------------------------------------------------

    build_context: Callable[[str, str, list[dict], Path | None], tuple[str, str]]
    """Return ``(model, context_prompt)`` for an agent.
    Signature: ``(role, agent_id, messages, worktree) → (model, context)``."""

    # -- Spawn -----------------------------------------------------------

    spawn_fn: Callable
    """``invoke.spawn(context, cwd, model, log_path) → (Popen, log_fh)``."""

    # -- Pending-work queries --------------------------------------------

    get_pending_visions: Callable[[], list[str]]
    """Return vision .md filenames with no matching board task.
    Used to decide whether a planner has anything to plan."""

    get_pending_reviews: Callable[[], list[str]]
    """Return feat/* branches not yet merged into dev.
    Used to decide whether there is in-flight coding work to review."""

    # -- Optional lifecycle hooks ----------------------------------------

    on_agent_start: Callable[[AgentProcess], None] | None = None
    """Called immediately after a new agent is added to the pool."""

    on_agent_done: Callable[[AgentProcess, int], None] | None = None
    """Called immediately after a completed agent is removed from the pool."""


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class Dispatcher:
    """Poll-based parallel agent scheduler.

    Parameters
    ----------
    squad:
        The squad configuration (agent counts, watchdog timeout).
    callbacks:
        Domain operations provided by the orchestrator.
    dry_run:
        When ``True`` the dispatcher prints agent contexts instead of
        spawning subprocesses.
    """

    def __init__(
        self,
        squad: SquadConfig,
        callbacks: DispatchCallbacks,
        *,
        dry_run: bool = False,
    ) -> None:
        self.squad = squad
        self.cb = callbacks
        self.dry_run = dry_run
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

    @property
    def total_agent_calls(self) -> int:
        """Total number of agent sessions spawned so far."""
        return self._total_spawned

    def run(self, maxcalls: int = 0) -> None:
        """Run the dispatch loop.

        *maxcalls* — maximum total agent invocations; ``0`` = unlimited.
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
            messages = self.cb.get_messages()

            # 1. Poll for completed agents.
            for agent, rc in self.pool.poll():
                self._handle_completion(agent, rc, messages)
                # Refresh messages after completion (agent may have posted).
                messages = self.cb.get_messages()

            # 2. Drain merge queue (serialized; one merge per cycle).
            if self._merge_queue:
                task = self._merge_queue.pop(0)
                self._do_merge(task)
                messages = self.cb.get_messages()

            # 3. Watchdog: kill stuck agents.
            timeout_sec = self.squad.timeout_minutes * 60.0
            for agent in self.pool.check_watchdog(timeout_sec):
                self._handle_watchdog(agent)

            # 4. Check for hard-blocked state (pauses all new dispatches).
            blocked_agent, blocked_state = self.cb.has_unresolved_block(messages)
            if blocked_agent and blocked_state == "blocked":
                self._handle_hard_block(blocked_agent, messages)
                messages = self.cb.get_messages()

            # 5. Dispatch new agents (skip when the call limit is already reached).
            at_limit = maxcalls > 0 and self._total_spawned >= maxcalls
            if not at_limit:
                if not self.dry_run or self._total_spawned == 0:
                    dispatched = self._dispatch(messages)
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
                logger.info("reached maxcalls and pool drained, stopping", maxcalls=maxcalls)
                typer.echo(f"\n↩ Reached --maxcalls {maxcalls}. Stopping.")
                break

            # Check idle-complete: nothing running, nothing to dispatch.
            if not at_limit and self.pool.is_empty() and dispatched == 0:
                if not Dispatcher.has_pending_work(self.cb, messages):
                    logger.info("no pending work and pool empty — workflow complete")
                    typer.echo("\n✓ No pending work. Workflow complete.")
                    break

            time.sleep(_POLL_INTERVAL)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, messages: list[dict]) -> int:
        """Spawn agents for all unassigned work. Returns number spawned."""
        dispatched = 0
        open_tasks = self.cb.get_open_tasks()

        if not open_tasks:
            # No open tasks — queue any unmerged feat/* branches for merge
            # and/or dispatch a planner for unplanned vision docs.
            # _do_merge handles clean merges automatically; a coder is only
            # spawned if there are conflicts.
            pending_reviews = self.cb.get_pending_reviews()
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
            if not self.cb.get_pending_visions():
                return dispatched
            if self.pool.count_by_role("planner") == 0:
                dispatched += self._spawn_planner(messages)
            return dispatched

        # Check for soft-block: route one planner to resolve it.
        blocked_agent, blocked_state = self.cb.has_unresolved_block(messages)
        if blocked_agent and blocked_state == "soft-blocked":
            if self.pool.count_by_role("planner") == 0:
                self._resolving_soft_block = (blocked_agent, blocked_state)
                agent_id = self._next_id("planner")
                self._spawn_agent("planner", agent_id, None, messages)
                dispatched += 1
            return dispatched

        # Dispatch coder/QA for each unassigned task up to squad capacity.
        for task in open_tasks:
            task_name = task["name"] if isinstance(task, dict) else str(task)
            assigned_to = task.get("assigned_to") if isinstance(task, dict) else None

            if assigned_to:
                continue  # already assigned to a running agent

            token, reason = self.cb.derive_task_state(task_name)
            logger.debug("task state", task=task_name, token=token, reason=reason)

            if token == QA_PASSED:
                if task_name not in self._merge_queue:
                    self._merge_queue.append(task_name)
                continue

            if token == CLOSE_BOARD:
                self.cb.do_close_board(task_name)
                continue

            if token not in ("coder", "qa"):
                continue

            # Check squad capacity for this role.
            if self.pool.count_by_role(token) >= self.squad.count(token):
                continue

            agent_id = self._next_id(token)
            self._spawn_agent(token, agent_id, task_name, messages)
            dispatched += 1

        return dispatched

    # ------------------------------------------------------------------
    # Agent spawn helpers
    # ------------------------------------------------------------------

    def _next_id(self, role: str) -> str:
        self._id_counters[role] += 1
        return tg.make_agent_id(role, self._id_counters[role])

    def _spawn_planner(self, messages: list[dict]) -> int:
        agent_id = self._next_id("planner")
        self._spawn_agent("planner", agent_id, None, messages)
        return 1

    def _spawn_agent(
        self,
        role: str,
        agent_id: str,
        task_name: str | None,
        messages: list[dict],
    ) -> None:
        """Build context and spawn an agent subprocess (or print for dry-run)."""
        if role == "planner":
            worktree = self.cb.ensure_dev_worktree()
        elif task_name:
            worktree = self.cb.ensure_feature_worktree(task_name)
        else:
            raise ValueError(f"No worktree: role={role!r} requires task_name")

        model, context = self.cb.build_context(role, agent_id, messages, worktree)

        if self.dry_run:
            typer.echo(f"Would spawn agent '{agent_id}' (model={model}, {len(context)} chars)")
            return

        body = self.cb.boot_message_body()
        self.cb.post_boot_message(agent_id, body)

        import structlog.contextvars as _cv

        _cv.bind_contextvars(agent_id=agent_id)

        log_path = AGENT_LOG_DIR / f"{agent_id}.log"
        spawn_result = self.cb.spawn_fn(context, worktree, model, log_path)

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
        if self.cb.on_agent_start is not None:
            self.cb.on_agent_start(agent)

        if task_name:
            self.cb.assign_task(task_name, agent_id)

        logger.info(
            "spawned agent",
            agent_id=agent_id,
            task=task_name,
            worktree=str(worktree),
            log=str(log_path),
        )
        typer.echo(f"\n⟳ Spawned {agent_id} (log: {log_path})")

    # ------------------------------------------------------------------
    # Completion handling
    # ------------------------------------------------------------------

    def _handle_completion(self, agent: AgentProcess, rc: int, messages: list[dict]) -> None:
        logger.info("agent exited", agent_id=agent.agent_id, exit_code=rc)
        self.pool.remove(agent.agent_id)
        self.pool.close_log(agent)
        _cleanup_context_tmp(agent.context_tmp)
        if self.cb.on_agent_done is not None:
            self.cb.on_agent_done(agent, rc)

        if rc != 0:
            logger.error(
                "agent failed", agent_id=agent.agent_id, exit_code=rc, log=str(agent.log_path)
            )
            typer.echo(
                f"\n✗ {agent.agent_id} exited with code {rc}. See {agent.log_path} for details."
            )
            if agent.task_name:
                self.cb.unassign_task(agent.task_name)
            return

        typer.echo(f"\n✓ {agent.agent_id} completed successfully.")

        if agent.task_name:
            self.cb.unassign_task(agent.task_name)

        # Post [orc](resolved) if this planner was resolving a soft-block.
        if agent.role == "planner" and self._resolving_soft_block is not None:
            blocked_a, blocked_s = self._resolving_soft_block
            self.cb.post_resolved(blocked_a, blocked_s, agent.agent_id)
            self._resolving_soft_block = None

    # ------------------------------------------------------------------
    # Merge (serialized)
    # ------------------------------------------------------------------

    def _do_merge(self, task_name: str) -> None:
        typer.echo(f"\n⟳ Merging {task_name} into dev…")
        try:
            self.cb.merge_feature(task_name)
            typer.echo(f"✓ {task_name} merged.")
        except Exception as exc:
            logger.error("merge failed", task=task_name, error=str(exc))
            typer.echo(f"\n✗ Merge failed for {task_name}: {exc}")

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
        typer.echo(
            f"\n⚠ {agent.agent_id} exceeded watchdog timeout "
            f"({elapsed_min:.0f} min > {self.squad.timeout_minutes} min). Killing."
        )
        self.pool.kill(agent.agent_id)
        self.pool.remove(agent.agent_id)
        _cleanup_context_tmp(agent.context_tmp)
        if agent.task_name:
            self.cb.unassign_task(agent.task_name)

    # ------------------------------------------------------------------
    # Hard-block handling
    # ------------------------------------------------------------------

    def _handle_hard_block(self, blocked_agent_id: str, messages: list[dict]) -> None:
        typer.echo(f"\n⏸  {blocked_agent_id}(blocked) — waiting for your reply in Telegram…")
        logger.info("hard block detected, waiting for human reply", agent=blocked_agent_id)
        try:
            human_reply = self.cb.wait_for_human_reply(messages)
        except TimeoutError:
            timeout_h = self.squad.timeout_minutes / 60.0
            timeout_msg = tg.format_agent_message(
                blocked_agent_id,
                "blocked",
                f"Stopped waiting for human reply after {timeout_h:.0f}h. Exiting.",
            )
            tg.send_message(timeout_msg)
            typer.echo("\n✗ Timed out waiting for human reply. Stopping.")
            raise typer.Exit(code=1)
        typer.echo(f"\n↩ Reply received: {human_reply[:80]!r}. Resuming…")
        # Post [orc](resolved) so _has_unresolved_block returns (None, None) on
        # the next cycle instead of seeing the block as still active.
        self.cb.post_resolved(blocked_agent_id, "blocked", "human-reply")

    # ------------------------------------------------------------------
    # Idle / done detection
    # ------------------------------------------------------------------

    @staticmethod
    def has_pending_work(cb: DispatchCallbacks, messages: list[dict]) -> bool:
        """Return True if there is any work that *could* be dispatched next cycle.

        Exposed as a public static method so callers (e.g. ``orc run``) can
        perform an early-exit check before entering the dispatch loop.
        """
        if cb.get_open_tasks():
            return True
        if cb.get_pending_visions() or cb.get_pending_reviews():
            return True
        # Check if blocked: hard-blocked means work exists but is stalled.
        blocked_agent, _ = cb.has_unresolved_block(messages)
        return blocked_agent is not None

    # ------------------------------------------------------------------
    # Graceful shutdown
    # ------------------------------------------------------------------

    def _kill_all_and_unassign(self) -> None:
        for agent in self.pool.all_agents():
            if agent.task_name:
                self.cb.unassign_task(agent.task_name)
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
