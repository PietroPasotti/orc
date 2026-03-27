"""Tests for orc/dispatcher.py — blocked state handling."""

from __future__ import annotations

from typer.testing import CliRunner

import orc.engine.dispatcher as _disp

runner = CliRunner()


class TestBlockedResumption:
    def test_plan_operation_runs_when_visions_exist(self, tmp_path, monkeypatch):
        """Pending visions are processed by the plan operation (not planner agent)."""
        from conftest import make_dispatcher, make_services, minimal_squad

        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        planned: list[str] = []
        from orc.engine.operations.plan import PlanResult, TaskSpec

        def _fake_plan(name, content, *, llm=None, existing_tasks=None):
            planned.append(name)
            return PlanResult(
                tasks=[TaskSpec(title="t", overview="o", steps=["s"])],
                vision_summary="summary",
            )

        monkeypatch.setattr("orc.engine.dispatcher.plan_vision", _fake_plan)

        svcs = make_services(
            tmp_path,
            get_pending_visions=lambda: ["feature-x.md"],
        )
        d = make_dispatcher(minimal_squad(), svcs)
        d._run_plan_operations()
        assert planned == ["feature-x.md"]
