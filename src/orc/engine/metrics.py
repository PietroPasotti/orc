"""Metrics collection for the orc orchestrator.

Provides :class:`MetricsCollector` — a lightweight in-process metrics
aggregator that records operational data such as agent spawn counts,
completion rates, and cycle durations.

Usage::

    collector = MetricsCollector()
    collector.agent_spawned("coder", "claude-sonnet-4.6")
    collector.agent_completed("coder", exit_code=0, duration=42.3)
    collector.cycle_completed(duration=55.1, agents_spawned=2)
    print(collector.summary())

The collector is deliberately simple (no threads, no external deps).  For
production Prometheus export, call :meth:`MetricsCollector.export_prometheus`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class MetricsCollector:
    """In-process metrics aggregator for the orc orchestrator.

    All state is stored in plain Python attributes — thread-safety is **not**
    guaranteed (the dispatcher runs in a single thread, so this is fine).
    """

    _agents_spawned: int = field(default=0, init=False)
    _agents_completed: int = field(default=0, init=False)
    _agents_failed: int = field(default=0, init=False)
    _cycles_completed: int = field(default=0, init=False)
    _total_agent_duration: float = field(default=0.0, init=False)
    _total_cycle_duration: float = field(default=0.0, init=False)
    _spawns_by_role: dict[str, int] = field(default_factory=dict, init=False)
    _failures_by_role: dict[str, int] = field(default_factory=dict, init=False)
    _started_at: float = field(default_factory=time.monotonic, init=False)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def agent_spawned(self, role: str, model: str) -> None:
        """Record that an agent was spawned."""
        self._agents_spawned += 1
        self._spawns_by_role[role] = self._spawns_by_role.get(role, 0) + 1
        logger.debug("metric: agent_spawned", role=role, model=model)

    def agent_completed(self, role: str, exit_code: int, duration: float) -> None:
        """Record that an agent completed (success or failure)."""
        if exit_code == 0:
            self._agents_completed += 1
        else:
            self._agents_failed += 1
            self._failures_by_role[role] = self._failures_by_role.get(role, 0) + 1
        self._total_agent_duration += duration
        logger.debug(
            "metric: agent_completed",
            role=role,
            exit_code=exit_code,
            duration=round(duration, 2),
        )

    def cycle_completed(self, duration: float, agents_spawned: int) -> None:
        """Record that a dispatch cycle completed."""
        self._cycles_completed += 1
        self._total_cycle_duration += duration
        logger.debug(
            "metric: cycle_completed",
            cycle=self._cycles_completed,
            duration=round(duration, 2),
            agents_spawned=agents_spawned,
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return a snapshot of all metrics as a plain dict."""
        return {
            "agents_spawned": self._agents_spawned,
            "agents_completed": self._agents_completed,
            "agents_failed": self._agents_failed,
            "cycles_completed": self._cycles_completed,
            "total_agent_duration_s": round(self._total_agent_duration, 2),
            "avg_cycle_duration_s": (
                round(self._total_cycle_duration / self._cycles_completed, 2)
                if self._cycles_completed
                else 0.0
            ),
            "spawns_by_role": dict(self._spawns_by_role),
            "failures_by_role": dict(self._failures_by_role),
            "uptime_s": round(time.monotonic() - self._started_at, 2),
        }

    def export_prometheus(self) -> str:
        """Return a Prometheus text-format metrics snapshot.

        Each metric is exported as a ``orc_`` prefixed gauge.
        """
        lines: list[str] = []
        s = self.summary()
        lines.append("# HELP orc_agents_spawned_total Total agents spawned")
        lines.append("# TYPE orc_agents_spawned_total counter")
        lines.append(f"orc_agents_spawned_total {s['agents_spawned']}")
        lines.append("# HELP orc_agents_completed_total Agents that exited with code 0")
        lines.append("# TYPE orc_agents_completed_total counter")
        lines.append(f"orc_agents_completed_total {s['agents_completed']}")
        lines.append("# HELP orc_agents_failed_total Agents that exited non-zero")
        lines.append("# TYPE orc_agents_failed_total counter")
        lines.append(f"orc_agents_failed_total {s['agents_failed']}")
        lines.append("# HELP orc_cycles_completed_total Dispatch cycles completed")
        lines.append("# TYPE orc_cycles_completed_total counter")
        lines.append(f"orc_cycles_completed_total {s['cycles_completed']}")
        lines.append("# HELP orc_uptime_seconds Orchestrator uptime in seconds")
        lines.append("# TYPE orc_uptime_seconds gauge")
        lines.append(f"orc_uptime_seconds {s['uptime_s']}")
        for role, count in s["spawns_by_role"].items():
            lines.append(f'orc_agents_spawned_by_role{{role="{role}"}} {count}')
        for role, count in s["failures_by_role"].items():
            lines.append(f'orc_agents_failed_by_role{{role="{role}"}} {count}')
        return "\n".join(lines) + "\n"
