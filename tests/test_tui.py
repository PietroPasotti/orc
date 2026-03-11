"""Tests for orc/tui.py."""

from __future__ import annotations

import io

import rich.console
import rich.live

from orc.tui import AgentRow, RunState, live_context, render


def _row(
    *,
    agent_id: str = "coder-1",
    role: str = "coder",
    model: str = "copilot",
    status: str = "running",
    task_name: str | None = "0001-foo.md",
    worktree: str = "/tmp/wt",
    started_at: float = 0.0,
) -> AgentRow:
    return AgentRow(
        agent_id=agent_id,
        role=role,
        model=model,
        status=status,
        task_name=task_name,
        worktree=worktree,
        started_at=started_at,
    )


def _render_to_str(state: RunState) -> str:
    buf = io.StringIO()
    console = rich.console.Console(file=buf, width=120, highlight=False)
    console.print(render(state))
    return buf.getvalue()


class TestRenderZeroAgents:
    def test_renders_without_agents(self):
        state = RunState()
        out = _render_to_str(state)
        assert "orc run" in out

    def test_footer_contains_loop_info(self):
        state = RunState(current_loop=3, max_loops=10)
        out = _render_to_str(state)
        assert "3" in out
        assert "10" in out

    def test_footer_unlimited_loops(self):
        state = RunState(current_loop=1, max_loops=0)
        out = _render_to_str(state)
        assert "∞" in out

    def test_footer_backend(self):
        state = RunState(backend="openai")
        out = _render_to_str(state)
        assert "openai" in out

    def test_footer_telegram_ok(self):
        state = RunState(telegram_ok=True)
        out = _render_to_str(state)
        assert "✓" in out

    def test_footer_telegram_not_ok(self):
        state = RunState(telegram_ok=False)
        out = _render_to_str(state)
        assert "✗" in out

    def test_footer_dev_ahead(self):
        state = RunState(dev_ahead=5)
        out = _render_to_str(state)
        assert "5" in out


class TestRenderOneAgent:
    def test_renders_agent_id(self):
        state = RunState(agents=[_row(agent_id="coder-1")])
        out = _render_to_str(state)
        assert "coder-1" in out

    def test_renders_model(self):
        state = RunState(agents=[_row(model="gpt-4")])
        out = _render_to_str(state)
        assert "gpt-4" in out

    def test_renders_status(self):
        state = RunState(agents=[_row(status="running")])
        out = _render_to_str(state)
        assert "running" in out

    def test_renders_task_name(self):
        state = RunState(agents=[_row(task_name="0002-bar.md")])
        out = _render_to_str(state)
        assert "0002-bar.md" in out

    def test_none_task_name_renders_dash(self):
        state = RunState(agents=[_row(task_name=None)])
        out = _render_to_str(state)
        assert "—" in out

    def test_renders_worktree(self):
        state = RunState(agents=[_row(worktree="/wt/path")])
        out = _render_to_str(state)
        assert "/wt/path" in out


class TestRenderRoles:
    def test_planner_role(self):
        state = RunState(agents=[_row(role="planner", task_name=None)])
        out = _render_to_str(state)
        assert "planner" in out

    def test_coder_role(self):
        state = RunState(agents=[_row(role="coder")])
        out = _render_to_str(state)
        assert "coder" in out

    def test_qa_role(self):
        state = RunState(agents=[_row(role="qa")])
        out = _render_to_str(state)
        assert "qa" in out


class TestRenderMultipleAgents:
    def test_two_agents(self):
        state = RunState(
            agents=[
                _row(agent_id="coder-1", role="coder"),
                _row(agent_id="qa-1", role="qa"),
            ]
        )
        out = _render_to_str(state)
        assert "coder-1" in out
        assert "qa-1" in out

    def test_three_agents_different_roles(self):
        state = RunState(
            agents=[
                _row(agent_id="planner-1", role="planner", task_name=None),
                _row(agent_id="coder-1", role="coder"),
                _row(agent_id="qa-1", role="qa"),
            ]
        )
        out = _render_to_str(state)
        assert "planner-1" in out
        assert "coder-1" in out
        assert "qa-1" in out


class TestLiveContext:
    def test_returns_live_instance(self):
        lc = live_context()
        assert isinstance(lc, rich.live.Live)

    def test_custom_refresh_rate(self):
        lc = live_context(refresh_per_second=2)
        assert isinstance(lc, rich.live.Live)
