"""Tests for orc/tui.py."""

from __future__ import annotations

import io
import threading
from unittest.mock import patch

import rich.console
import rich.panel

from orc.cli.tui.run_tui import (
    AgentData,
    OrcApp,
    OrcData,
    RunState,
    _agent_card,
    _column_panel,
    _elapsed,
    _orc_card,
    render,
    run_tui,
)


def _row(
    *,
    agent_id: str = "coder-1",
    role: str = "coder",
    model: str = "copilot",
    status: str = "running",
    task_name: str | None = "0001-foo.md",
    worktree: str = "/tmp/wt",
    started_at: float = 0.0,
) -> AgentData:
    return AgentData(
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


def _panel_to_str(panel: rich.panel.Panel) -> str:
    buf = io.StringIO()
    console = rich.console.Console(file=buf, width=80, highlight=False)
    console.print(panel)
    return buf.getvalue()


class TestElapsed:
    def test_zero_seconds(self):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            assert _elapsed(0.0) == "0m 0s"

    def test_ninety_seconds(self):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 90.0
            assert _elapsed(0.0) == "1m 30s"

    def test_3661_seconds(self):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 3661.0
            assert _elapsed(0.0) == "61m 1s"


class TestAgentCard:
    def test_title_is_agent_id(self):
        row = _row(agent_id="planner-1")
        card = _agent_card(row)
        assert card.title == "planner-1"

    def test_body_contains_status(self):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            card = _agent_card(_row(status="done"))
        out = _panel_to_str(card)
        assert "done" in out

    def test_body_contains_task_name(self):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            card = _agent_card(_row(task_name="0002-bar.md"))
        out = _panel_to_str(card)
        assert "0002-bar.md" in out

    def test_none_task_name_shows_dash(self):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            card = _agent_card(_row(task_name=None))
        out = _panel_to_str(card)
        assert "—" in out

    def test_body_contains_worktree_basename(self):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            card = _agent_card(_row(worktree="/some/path/myworktree"))
        out = _panel_to_str(card)
        assert "myworktree" in out

    def test_body_contains_elapsed(self):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 90.0
            card = _agent_card(_row(started_at=0.0))
        out = _panel_to_str(card)
        assert "1m 30s" in out


class TestOrcCard:
    def test_title_is_agent_id(self):
        data = OrcData(agent_id="orc-0", status="running", task="planning")
        card = _orc_card(data)
        assert card.title == "orc-0"

    def test_body_contains_status(self):
        data = OrcData(agent_id="orc-0", status="waiting", task=None)
        out = _panel_to_str(_orc_card(data))
        assert "waiting" in out

    def test_body_contains_task(self):
        data = OrcData(agent_id="orc-0", status="running", task="dispatching")
        out = _panel_to_str(_orc_card(data))
        assert "dispatching" in out

    def test_none_task_shows_dash(self):
        data = OrcData(agent_id="orc-0", status="idle", task=None)
        out = _panel_to_str(_orc_card(data))
        assert "—" in out

    def test_empty_rows_shows_idle(self):
        panel = _column_panel("Coder", [])
        out = _panel_to_str(panel)
        assert "(idle)" in out

    def test_single_row_title_includes_role_and_model(self):
        panel = _column_panel("Coder", [_row(model="gpt-4")])
        out = _panel_to_str(panel)
        assert "Coder" in out
        assert "gpt-4" in out

    def test_same_model_shows_model_name(self):
        rows = [_row(model="claude"), _row(agent_id="coder-2", model="claude")]
        panel = _column_panel("Coder", rows)
        out = _panel_to_str(panel)
        assert "claude" in out
        assert "(mixed)" not in out

    def test_different_models_shows_mixed(self):
        rows = [_row(model="gpt-4"), _row(agent_id="coder-2", model="claude")]
        panel = _column_panel("Coder", rows)
        out = _panel_to_str(panel)
        assert "(mixed)" in out

    def test_empty_title_has_no_model_string(self):
        panel = _column_panel("Planner", [])
        out = _panel_to_str(panel)
        assert "Planner" in out


class TestRenderZeroAgents:
    def test_renders_without_agents(self):
        state = RunState()
        out = _render_to_str(state)
        assert "calls 0/∞" in out

    def test_all_three_columns_present(self):
        state = RunState()
        out = _render_to_str(state)
        assert "Planner" in out
        assert "Coder" in out
        assert "QA" in out

    def test_empty_columns_show_idle(self):
        state = RunState()
        out = _render_to_str(state)
        assert "(idle)" in out

    def test_header_contains_loop_info(self):
        state = RunState(current_calls=3, max_calls=10)
        out = _render_to_str(state)
        assert "calls 3/10" in out

    def test_header_unlimited_loops(self):
        state = RunState(current_calls=1, max_calls=0)
        out = _render_to_str(state)
        assert "∞" in out

    def test_header_backend(self):
        state = RunState(backend="openai")
        out = _render_to_str(state)
        assert "openai" in out

    def test_header_telegram_ok(self):
        state = RunState(telegram_ok=True)
        out = _render_to_str(state)
        assert "✓" in out

    def test_header_telegram_not_ok(self):
        state = RunState(telegram_ok=False)
        out = _render_to_str(state)
        assert "✗" in out

    def test_header_features_done(self):
        state = RunState(features_done=5)
        out = _render_to_str(state)
        assert "5 features" in out

    def test_orc_card_shown_when_present(self):
        state = RunState(orc=OrcData(agent_id="orc-0", status="running", task="orchestrating"))
        out = _render_to_str(state)
        assert "orc-0" in out
        assert "orchestrating" in out

    def test_orc_card_absent_when_none(self):
        state = RunState(orc=None)
        out = _render_to_str(state)
        assert "orc-0" not in out

    def test_one_planner_two_coders_one_qa(self):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            state = RunState(
                agents=[
                    _row(agent_id="planner-1", role="planner", task_name=None),
                    _row(agent_id="coder-1", role="coder"),
                    _row(agent_id="coder-2", role="coder"),
                    _row(agent_id="qa-1", role="qa"),
                ]
            )
            out = _render_to_str(state)
        assert "planner-1" in out
        assert "coder-1" in out
        assert "coder-2" in out
        assert "qa-1" in out
        assert "Planner" in out
        assert "Coder" in out
        assert "QA" in out

    def test_renders_agent_id(self):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            state = RunState(agents=[_row(agent_id="coder-1")])
            out = _render_to_str(state)
        assert "coder-1" in out

    def test_renders_model(self):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            state = RunState(agents=[_row(model="gpt-4")])
            out = _render_to_str(state)
        assert "gpt-4" in out

    def test_renders_status(self):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            state = RunState(agents=[_row(status="running")])
            out = _render_to_str(state)
        assert "running" in out

    def test_renders_task_name(self):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            state = RunState(agents=[_row(task_name="0002-bar.md")])
            out = _render_to_str(state)
        assert "0002-bar.md" in out

    def test_none_task_name_renders_dash(self):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            state = RunState(agents=[_row(task_name=None)])
            out = _render_to_str(state)
        assert "—" in out

    def test_renders_worktree_basename(self):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            state = RunState(agents=[_row(worktree="/wt/mypath")])
            out = _render_to_str(state)
        assert "mypath" in out


class TestRenderRoles:
    def test_planner_role(self):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            state = RunState(agents=[_row(role="planner", task_name=None)])
            out = _render_to_str(state)
        assert "Planner" in out

    def test_coder_role(self):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            state = RunState(agents=[_row(role="coder")])
            out = _render_to_str(state)
        assert "Coder" in out

    def test_qa_role(self):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            state = RunState(agents=[_row(role="qa")])
            out = _render_to_str(state)
        assert "QA" in out


class TestRenderMultipleAgents:
    def test_two_agents(self):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
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
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
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


class TestRunTui:
    def test_run_tui_calls_run_fn(self):
        """run_tui executes run_fn in a background thread."""
        called = []

        def run_fn() -> None:
            called.append(True)

        with patch.object(OrcApp, "run", return_value=None):
            run_tui(RunState(), run_fn)

        assert called == [True]

    def test_run_tui_reraises_exception(self):
        """run_tui propagates exceptions raised by run_fn."""
        import pytest

        def boom() -> None:
            raise RuntimeError("dispatch failed")

        with patch.object(OrcApp, "run", return_value=None):
            with pytest.raises(RuntimeError, match="dispatch failed"):
                run_tui(RunState(), boom)

    def test_orc_app_is_app_instance(self):
        """OrcApp can be instantiated."""
        from textual.app import App

        t = threading.Thread(target=lambda: None)
        app = OrcApp(RunState(), t)
        assert isinstance(app, App)


class TestOrcAppMethods:
    """Unit-test OrcApp internals without a live event loop."""

    def test_compose_yields_static(self):
        """compose() yields a Static widget with the initial render."""
        from textual.widgets import Static

        t = threading.Thread(target=lambda: None)
        app = OrcApp(RunState(), t)
        widgets = list(app.compose())
        assert len(widgets) == 1
        assert isinstance(widgets[0], Static)
        assert widgets[0].id == "display"

    def test_on_mount_sets_interval(self):
        """on_mount() calls set_interval."""
        t = threading.Thread(target=lambda: None)
        app = OrcApp(RunState(), t)
        intervals: list = []
        app.set_interval = lambda secs, fn: intervals.append((secs, fn))
        app.on_mount()
        assert len(intervals) == 1
        assert intervals[0][0] == 0.25

    def test_refresh_exits_when_worker_done(self):
        """_refresh() calls self.exit() when the worker thread is no longer alive."""
        t = threading.Thread(target=lambda: None)
        t.start()
        t.join()  # ensure it's dead

        app = OrcApp(RunState(), t)
        exited = []
        app.exit = lambda: exited.append(True)

        class FakeStatic:
            def update(self, renderable: object) -> None:
                pass

        app.query_one = lambda selector, widget_type: FakeStatic()
        app._refresh()
        assert exited == [True]

    def test_refresh_updates_display(self):
        """_refresh() updates the Static widget with the current render."""
        t = threading.Thread(target=lambda: None)
        t.start()
        t.join()

        state = RunState(current_calls=5)
        app = OrcApp(state, t)
        app.exit = lambda: None
        updates: list = []

        class FakeStatic:
            def update(self, renderable: object) -> None:
                updates.append(renderable)

        app.query_one = lambda selector, widget_type: FakeStatic()
        app._refresh()
        assert updates
