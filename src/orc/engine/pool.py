"""Agent process pool for the orc parallel orchestrator.

Manages running agent subprocesses spawned by :mod:`orc.dispatcher`.

Each agent is represented by an :class:`AgentProcess` dataclass that
records the subprocess handle, its log file, the task it is working on,
and when it was started (for watchdog purposes).

:class:`AgentPool` is a thin container that provides poll/watchdog/kill
helpers on top of a ``dict[agent_id → AgentProcess]``.

Log layout::

    .orc//{agent_id}.log

One file per agent invocation; truncated (not appended) at each spawn so the
log always contains only the most recent run.  The base directory comes from
:data:`orc.config.LOG_DIR` so it respects ``orc-log-dir`` in ``config.yaml``
and the ``ORC_LOG_DIR`` environment variable.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

import structlog

from orc.squad import AgentRole

logger = structlog.get_logger(__name__)


@dataclass
class AgentProcess:
    """A running agent subprocess with associated metadata."""

    agent_id: str
    """Unique identifier in the form ``{role}-{n}`` (e.g. ``coder-1``)."""

    role: AgentRole
    """The agent's role."""

    model: str
    """The AI model used by this agent (e.g. ``copilot``)."""

    task_name: str | None
    """Name of the board task this agent is working on, or ``None`` for the planner."""

    process: subprocess.Popen[bytes]
    """The underlying subprocess handle."""

    worktree: Path
    """The git worktree the agent is running in."""

    log_path: Path
    """Path to the agent's log file."""

    log_fh: IO[str] | None
    """Open file handle for the log (kept open while the process runs)."""

    context_tmp: str | None
    """Path to the temporary prompt file; deleted when the agent is cleaned up."""

    started_at: float = field(default_factory=time.monotonic)
    """Monotonic timestamp of when the agent was spawned."""


class AgentPool:
    """Tracks a set of running agent subprocesses.

    Thread-safety: **not** thread-safe.  The dispatcher runs in a single
    thread (poll loop), so no locking is needed.
    """

    def __init__(self) -> None:
        self._agents: dict[str, AgentProcess] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, agent: AgentProcess) -> None:
        """Register a new running agent."""
        self._agents[agent.agent_id] = agent

    def remove(self, agent_id: str) -> AgentProcess | None:
        """Deregister an agent and return it (or ``None`` if not found)."""
        return self._agents.pop(agent_id, None)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, agent_id: str) -> AgentProcess | None:
        return self._agents.get(agent_id)

    def all_agents(self) -> list[AgentProcess]:
        return list(self._agents.values())

    def is_empty(self) -> bool:
        return not self._agents

    def count_by_role(self, role: AgentRole | str) -> int:
        return sum(1 for a in self._agents.values() if a.role == role)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def poll(self) -> list[tuple[AgentProcess, int]]:
        """Check all running agents for completion.

        Returns a list of ``(agent, exit_code)`` for every agent that has
        finished since the last poll.  Completed agents are **not**
        automatically removed from the pool; call :meth:`remove` after
        processing each completion.
        """
        completed: list[tuple[AgentProcess, int]] = []
        for agent in list(self._agents.values()):
            rc = agent.process.poll()
            if rc is not None:
                completed.append((agent, rc))
        return completed

    def check_watchdog(self, timeout_seconds: float) -> list[AgentProcess]:
        """Return agents that have been running longer than *timeout_seconds*."""
        now = time.monotonic()
        return [a for a in self._agents.values() if (now - a.started_at) > timeout_seconds]

    def kill(self, agent_id: str) -> None:
        """Kill the agent process and close its log file handle.

        Safe to call if the agent has already exited.
        """
        agent = self._agents.get(agent_id)
        if agent is None:
            return
        try:
            agent.process.kill()
            agent.process.wait(timeout=10)
        except Exception:
            pass
        _close_log(agent)

    def kill_all(self) -> None:
        """Kill every running agent (used on graceful shutdown)."""
        for agent_id in list(self._agents):
            self.kill(agent_id)
        self._agents.clear()

    def close_log(self, agent: AgentProcess) -> None:
        """Close the log file handle for *agent* (idempotent)."""
        _close_log(agent)


def _close_log(agent: AgentProcess) -> None:
    """Close *agent*'s log file handle if it is open."""
    if agent.log_fh is not None:
        try:
            agent.log_fh.close()
        except Exception:
            pass
        agent.log_fh = None
