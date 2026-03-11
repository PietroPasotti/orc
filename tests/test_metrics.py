"""Tests for orc/metrics.py — MetricsCollector."""

from __future__ import annotations

from orc.metrics import MetricsCollector


class TestMetricsCollector:
    def test_initial_state(self):
        c = MetricsCollector()
        s = c.summary()
        assert s["agents_spawned"] == 0
        assert s["agents_completed"] == 0
        assert s["agents_failed"] == 0
        assert s["cycles_completed"] == 0
        assert s["total_agent_duration_s"] == 0.0
        assert s["avg_cycle_duration_s"] == 0.0
        assert s["spawns_by_role"] == {}
        assert s["failures_by_role"] == {}

    def test_agent_spawned(self):
        c = MetricsCollector()
        c.agent_spawned("coder", "claude-3")
        c.agent_spawned("coder", "claude-3")
        c.agent_spawned("qa", "gpt-4")
        s = c.summary()
        assert s["agents_spawned"] == 3
        assert s["spawns_by_role"]["coder"] == 2
        assert s["spawns_by_role"]["qa"] == 1

    def test_agent_completed_success(self):
        c = MetricsCollector()
        c.agent_completed("coder", 0, 30.0)
        s = c.summary()
        assert s["agents_completed"] == 1
        assert s["agents_failed"] == 0
        assert s["total_agent_duration_s"] == 30.0

    def test_agent_completed_failure(self):
        c = MetricsCollector()
        c.agent_completed("coder", 1, 5.0)
        s = c.summary()
        assert s["agents_completed"] == 0
        assert s["agents_failed"] == 1
        assert s["failures_by_role"]["coder"] == 1

    def test_cycle_completed(self):
        c = MetricsCollector()
        c.cycle_completed(10.0, 2)
        c.cycle_completed(20.0, 1)
        s = c.summary()
        assert s["cycles_completed"] == 2
        assert s["avg_cycle_duration_s"] == 15.0

    def test_uptime_is_positive(self):
        c = MetricsCollector()
        s = c.summary()
        assert s["uptime_s"] >= 0.0

    def test_export_prometheus_contains_metrics(self):
        c = MetricsCollector()
        c.agent_spawned("coder", "test-model")
        c.agent_completed("coder", 0, 10.0)
        c.cycle_completed(15.0, 1)
        prom = c.export_prometheus()
        assert "orc_agents_spawned_total" in prom
        assert "orc_agents_completed_total" in prom
        assert "orc_agents_failed_total" in prom
        assert "orc_cycles_completed_total" in prom
        assert "orc_uptime_seconds" in prom
        assert 'role="coder"' in prom

    def test_export_prometheus_failure_by_role(self):
        c = MetricsCollector()
        c.agent_completed("qa", 1, 2.0)
        prom = c.export_prometheus()
        assert 'orc_agents_failed_by_role{role="qa"}' in prom

    def test_multiple_failures_same_role(self):
        c = MetricsCollector()
        c.agent_completed("coder", 1, 1.0)
        c.agent_completed("coder", 1, 2.0)
        s = c.summary()
        assert s["failures_by_role"]["coder"] == 2

    def test_avg_cycle_zero_when_no_cycles(self):
        c = MetricsCollector()
        assert c.summary()["avg_cycle_duration_s"] == 0.0
