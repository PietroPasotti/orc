"""Tests for orc/tui.py."""

from __future__ import annotations

import io
import threading
from unittest.mock import patch

import pytest
import rich.console
import rich.panel

from orc.cli.tui.run_tui import (
    AgentData,
    OrcApp,
    OrcData,
    QuitModal,
    RunState,
    _agent_card,
    _column_panel,
    _elapsed,
    _format_duration,
    _orc_card,
    _print_exit_summary,
    format_exit_summary,
    format_run_summary,
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
    @pytest.mark.parametrize(
        "now,expected",
        [
            (0.0, "0m 0s"),
            (90.0, "1m 30s"),
            (3661.0, "61m 1s"),
        ],
    )
    def test_elapsed(self, now, expected):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = now
            assert _elapsed(0.0) == expected


class TestAgentCard:
    def test_title_is_agent_id(self):
        row = _row(agent_id="planner-1")
        card = _agent_card(row)
        assert card.title == "planner-1"

    @pytest.mark.parametrize(
        "kwargs,expected_in_body",
        [
            ({"status": "done"}, "done"),
            ({"task_name": "0002-bar.md"}, "0002-bar.md"),
            ({"worktree": "/some/path/myworktree"}, "myworktree"),
            ({"started_at": 0.0}, "1m 30s"),  # when mock_time.monotonic.return_value = 90.0
        ],
    )
    def test_body_contains(self, kwargs, expected_in_body):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            # For elapsed test, set mock time to 90.0
            if "started_at" in kwargs:
                mock_time.monotonic.return_value = 90.0
            else:
                mock_time.monotonic.return_value = 0.0
            card = _agent_card(_row(**kwargs))
        out = _panel_to_str(card)
        assert expected_in_body in out

    def test_none_task_name_shows_dash(self):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            card = _agent_card(_row(task_name=None))
        out = _panel_to_str(card)
        assert "—" in out

    def test_details_shown_when_set(self):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            row = AgentData(
                agent_id="planner-1",
                role="planner",
                model="copilot",
                status="running",
                task_name=None,
                worktree="/tmp/wt",
                started_at=0.0,
                details="3 todo(s)  visions: 0001-foo",
            )
            card = _agent_card(row)
        out = _panel_to_str(card)
        assert "3 todo(s)" in out
        assert "visions: 0001-foo" in out

    def test_details_absent_when_none(self):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            row = AgentData(
                agent_id="coder-1",
                role="coder",
                model="copilot",
                status="running",
                task_name="0001-foo.md",
                worktree="/tmp/wt",
                started_at=0.0,
                details=None,
            )
            card = _agent_card(row)
        out = _panel_to_str(card)
        assert "todo" not in out
        assert "visions" not in out


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

    def test_header_squad_name(self):
        state = RunState(squad_repr="broad (1-4-1)")
        out = _render_to_str(state)
        assert "squad=broad (1-4-1)" in out

    def test_header_no_squad_when_empty(self):
        state = RunState(squad_repr="")
        out = _render_to_str(state)
        assert "squad=" not in out

    def test_header_runtime_shown(self):
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 130.0
            state = RunState(run_started_at=60.0)
            out = _render_to_str(state)
        assert "runtime 1m 10s" in out

    def test_header_no_runtime_when_zero(self):
        state = RunState(run_started_at=0.0)
        out = _render_to_str(state)
        assert "runtime" not in out

    def test_header_features_done(self):
        state = RunState(features_done=5)
        out = _render_to_str(state)
        assert "5 features" in out

    def test_header_separators_between_labels(self):
        state = RunState(
            current_calls=3,
            max_calls=10,
            features_done=2,
            backend="copilot",
            telegram_ok=True,
        )
        out = _render_to_str(state)
        assert "│" in out

    def test_header_separator_count_matches_labels(self):
        """Four always-present labels → three separators between them."""
        state = RunState(
            current_calls=1,
            max_calls=5,
            features_done=0,
            backend="copilot",
            telegram_ok=False,
            squad_repr="",
            run_started_at=0.0,
            stuck_tasks=0,
            draining=False,
        )
        out = _render_to_str(state)
        # Count │ only in header lines (before the panel borders start with ╭)
        header = out.split("╭")[0]
        assert header.count("│") == 3

    def test_header_separator_with_optional_labels(self):
        """Conditional labels add extra separators when present."""
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 130.0
            state = RunState(
                current_calls=1,
                max_calls=5,
                features_done=0,
                backend="copilot",
                telegram_ok=False,
                squad_repr="default (1-4-1)",
                run_started_at=60.0,
                stuck_tasks=2,
                draining=True,
            )
            out = _render_to_str(state)
        # 4 always-present + 4 conditional = 8 labels → 7 separators
        header = out.split("╭")[0]
        assert header.count("│") == 7

    def test_orc_card_shown_when_present(self):
        state = RunState(orc=OrcData(agent_id="orc-0", status="running", task="orchestrating"))
        out = _render_to_str(state)
        assert "orc-0" in out
        assert "orchestrating" in out

    def test_orc_card_absent_when_none(self):
        state = RunState(orc=None)
        out = _render_to_str(state)
        assert "orc-0" not in out

    def test_header_shows_squad_repr(self):
        state = RunState(squad_repr="default (1-4-1)")
        out = _render_to_str(state)
        assert "squad=default (1-4-1)" in out

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
        """compose() yields a Static widget with the initial render and a footer."""
        from textual.widgets import Static

        t = threading.Thread(target=lambda: None)
        app = OrcApp(RunState(), t)
        widgets = list(app.compose())
        assert len(widgets) == 2
        assert isinstance(widgets[0], Static)
        assert widgets[0].id == "display"
        assert isinstance(widgets[1], Static)
        assert widgets[1].id == "footer-bar"

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


class TestDrainIndicator:
    """Tests for the drain-mode indicator in the TUI header."""

    def test_header_includes_draining_when_active(self):
        state = RunState(draining=True)
        out = _render_to_str(state)
        assert "⏳ draining…" in out

    def test_header_excludes_draining_when_inactive(self):
        state = RunState(draining=False)
        out = _render_to_str(state)
        assert "draining" not in out

    def test_draining_default_is_false(self):
        state = RunState()
        assert state.draining is False


class TestOrcAppDrain:
    """Tests for the OrcApp quit modal and drain/abort callbacks."""

    def test_action_request_quit_opens_modal_when_drain_configured(self):
        """action_request_quit() pushes QuitModal when on_drain is set."""
        screens_pushed: list = []
        t = threading.Thread(target=lambda: None)
        app = OrcApp(RunState(), t, on_drain=lambda: None)
        app.push_screen = lambda screen, callback=None: screens_pushed.append((screen, callback))
        app.action_request_quit()
        assert len(screens_pushed) == 1
        assert isinstance(screens_pushed[0][0], QuitModal)

    def test_action_request_quit_exits_without_drain_callback(self):
        """action_request_quit() falls back to self.exit() when no drain callback."""
        exited = []
        t = threading.Thread(target=lambda: None)
        app = OrcApp(RunState(), t)
        app.exit = lambda: exited.append(True)
        app.action_request_quit()
        assert exited == [True]

    def test_handle_quit_choice_drain(self):
        """_handle_quit_choice('drain') calls on_drain."""
        drained = []
        t = threading.Thread(target=lambda: None)
        app = OrcApp(RunState(), t, on_drain=lambda: drained.append(True))
        app._handle_quit_choice("drain")
        assert drained == [True]

    def test_handle_quit_choice_abort(self):
        """_handle_quit_choice('abort') calls on_abort and exits."""
        aborted = []
        exited = []
        t = threading.Thread(target=lambda: None)
        app = OrcApp(
            RunState(),
            t,
            on_drain=lambda: None,
            on_abort=lambda: aborted.append(True),
        )
        app.exit = lambda: exited.append(True)
        app._handle_quit_choice("abort")
        assert aborted == [True]
        assert exited == [True]

    def test_handle_quit_choice_cancel(self):
        """_handle_quit_choice('') (cancel) does nothing."""
        drained = []
        aborted = []
        exited = []
        t = threading.Thread(target=lambda: None)
        app = OrcApp(
            RunState(),
            t,
            on_drain=lambda: drained.append(True),
            on_abort=lambda: aborted.append(True),
        )
        app.exit = lambda: exited.append(True)
        app._handle_quit_choice("")
        assert drained == []
        assert aborted == []
        assert exited == []

    def test_orc_app_stores_on_drain(self):
        """OrcApp stores the on_drain callback."""
        cb = lambda: None  # noqa: E731
        t = threading.Thread(target=lambda: None)
        app = OrcApp(RunState(), t, on_drain=cb)
        assert app._on_drain is cb

    def test_orc_app_stores_on_abort(self):
        """OrcApp stores the on_abort callback."""
        cb = lambda: None  # noqa: E731
        t = threading.Thread(target=lambda: None)
        app = OrcApp(RunState(), t, on_abort=cb)
        assert app._on_abort is cb

    def test_orc_app_on_drain_default_none(self):
        """OrcApp defaults on_drain to None."""
        t = threading.Thread(target=lambda: None)
        app = OrcApp(RunState(), t)
        assert app._on_drain is None

    def test_orc_app_on_abort_default_none(self):
        """OrcApp defaults on_abort to None."""
        t = threading.Thread(target=lambda: None)
        app = OrcApp(RunState(), t)
        assert app._on_abort is None


class TestQuitModal:
    """Tests for the QuitModal screen."""

    def test_is_modal_screen(self):
        """QuitModal is a ModalScreen."""
        from textual.screen import ModalScreen

        modal = QuitModal()
        assert isinstance(modal, ModalScreen)

    def test_dismiss_drain_on_button(self):
        """Pressing drain button dismisses with 'drain'."""
        dismissed = []
        modal = QuitModal()
        modal.dismiss = lambda result: dismissed.append(result)

        from textual.widgets import Button

        event = Button.Pressed(Button("", id="btn-drain"))
        modal.on_button_pressed(event)
        assert dismissed == ["drain"]

    def test_dismiss_abort_on_button(self):
        """Pressing abort button dismisses with 'abort'."""
        dismissed = []
        modal = QuitModal()
        modal.dismiss = lambda result: dismissed.append(result)

        from textual.widgets import Button

        event = Button.Pressed(Button("", id="btn-abort"))
        modal.on_button_pressed(event)
        assert dismissed == ["abort"]

    def test_cancel_dismisses_empty(self):
        """Pressing escape dismisses with empty string."""
        dismissed = []
        modal = QuitModal()
        modal.dismiss = lambda result: dismissed.append(result)
        modal.action_cancel()
        assert dismissed == [""]

    def test_has_escape_binding(self):
        """QuitModal has an escape binding for cancel."""
        bindings = {b.key for b in QuitModal.BINDINGS}
        assert "escape" in bindings


class TestRunTuiDrain:
    """Tests for run_tui with drain/abort callbacks."""

    def test_run_tui_passes_drain_to_app(self):
        """run_tui passes on_drain to OrcApp."""
        drained = []

        def run_fn() -> None:
            pass

        with patch.object(OrcApp, "run", return_value=None):
            # We can't easily inspect the OrcApp instance, but we can
            # verify the function doesn't crash with the new parameter.
            run_tui(RunState(), run_fn, on_drain=lambda: drained.append(True))

    def test_run_tui_passes_abort_to_app(self):
        """run_tui passes on_abort to OrcApp."""
        aborted = []

        def run_fn() -> None:
            pass

        with patch.object(OrcApp, "run", return_value=None):
            run_tui(
                RunState(),
                run_fn,
                on_drain=lambda: None,
                on_abort=lambda: aborted.append(True),
            )

    def test_run_tui_without_drain_works(self):
        """run_tui works without on_drain (backward compatible)."""
        called = []

        def run_fn() -> None:
            called.append(True)

        with patch.object(OrcApp, "run", return_value=None):
            run_tui(RunState(), run_fn)

        assert called == [True]


class TestFormatDuration:
    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (0, "0m 0s"),
            (59, "0m 59s"),
            (60, "1m 0s"),
            (90.7, "1m 30s"),
            (3661, "61m 1s"),
        ],
    )
    def test_format_duration(self, seconds, expected):
        assert _format_duration(seconds) == expected


class TestFormatExitSummary:
    def test_completed_no_agents(self):
        state = RunState(current_calls=5, max_calls=10, features_done=2)
        out = format_exit_summary(state, elapsed_seconds=90.0)
        assert "✓ completed" in out
        assert "1m 30s" in out
        assert "5/10" in out
        assert "2 done" in out
        assert "agents:   none" in out

    def test_error_summary(self):
        state = RunState()
        err = RuntimeError("dispatch failed")
        out = format_exit_summary(state, elapsed_seconds=5.0, error=err)
        assert "✗ error" in out
        assert "RuntimeError" in out
        assert "dispatch failed" in out

    def test_unlimited_max_calls(self):
        state = RunState(current_calls=3, max_calls=0)
        out = format_exit_summary(state, elapsed_seconds=0.0)
        assert "3/∞" in out

    def test_agents_counted_by_role(self):
        state = RunState(
            agents=[
                _row(agent_id="coder-1", role="coder"),
                _row(agent_id="coder-2", role="coder"),
                _row(agent_id="qa-1", role="qa"),
            ],
        )
        out = format_exit_summary(state, elapsed_seconds=0.0)
        assert "2 coder" in out
        assert "1 qa" in out

    def test_stuck_shown_when_nonzero(self):
        state = RunState(stuck_tasks=3)
        out = format_exit_summary(state, elapsed_seconds=0.0)
        assert "stuck:    3" in out

    def test_stuck_hidden_when_zero(self):
        state = RunState(stuck_tasks=0)
        out = format_exit_summary(state, elapsed_seconds=0.0)
        assert "stuck" not in out

    def test_features_done_shown(self):
        state = RunState(features_done=7)
        out = format_exit_summary(state, elapsed_seconds=0.0)
        assert "7 done" in out


class TestPrintExitSummary:
    def test_prints_panel_to_stdout(self, capsys):
        state = RunState(current_calls=2, max_calls=5, features_done=1)
        _print_exit_summary(state, elapsed_seconds=60.0)
        captured = capsys.readouterr()
        assert "orc run summary" in captured.out
        assert "✓ completed" in captured.out

    def test_prints_error_panel(self, capsys):
        state = RunState()
        _print_exit_summary(state, elapsed_seconds=0.0, error=ValueError("boom"))
        captured = capsys.readouterr()
        assert "✗ error" in captured.out
        assert "boom" in captured.out


class TestRunTuiSummary:
    def test_run_tui_prints_summary_on_success(self, capsys):
        """run_tui prints exit summary after successful run."""

        def run_fn() -> None:
            pass

        with patch.object(OrcApp, "run", return_value=None):
            run_tui(RunState(current_calls=3, max_calls=10), run_fn)

        captured = capsys.readouterr()
        assert "orc run summary" in captured.out
        assert "✓ completed" in captured.out

    def test_run_tui_prints_summary_on_error(self, capsys):
        """run_tui prints exit summary even when run_fn raises."""

        def boom() -> None:
            raise RuntimeError("dispatch failed")

        with patch.object(OrcApp, "run", return_value=None):
            with pytest.raises(RuntimeError, match="dispatch failed"):
                run_tui(RunState(), boom)

        captured = capsys.readouterr()
        assert "orc run summary" in captured.out
        assert "✗ error" in captured.out


def _summary_to_str(state: RunState) -> str:
    """Render format_run_summary output to a plain string."""
    buf = io.StringIO()
    console = rich.console.Console(file=buf, width=120, highlight=False)
    console.print(format_run_summary(state))
    return buf.getvalue()


class TestFormatRunSummary:
    """Tests for the post-exit run summary."""

    def test_fully_populated_state(self):
        """Summary contains all expected fields for a fully-populated RunState."""
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 190.0
            state = RunState(
                current_calls=7,
                max_calls=10,
                features_done=3,
                stuck_tasks=1,
                backend="copilot",
                squad_name="broad",
                run_started_at=10.0,
                planner_calls=2,
                coder_calls=4,
                qa_calls=1,
            )
            out = _summary_to_str(state)

        assert "3m 0s" in out
        assert "7" in out  # total calls
        assert "planner" in out.lower()
        assert "coder" in out.lower()
        assert "qa" in out.lower()
        assert "2" in out  # planner calls
        assert "4" in out  # coder calls
        assert "1" in out  # qa calls (and stuck tasks)
        assert "3" in out  # features merged
        assert "broad" in out
        assert "copilot" in out

    def test_minimal_state(self):
        """Summary works for default/empty RunState."""
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            state = RunState()
            out = _summary_to_str(state)

        assert "0m 0s" in out
        assert "0" in out

    def test_per_role_counters_shown(self):
        """Per-role call counters appear in the summary output."""
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 60.0
            state = RunState(
                run_started_at=0.0,
                planner_calls=5,
                coder_calls=10,
                qa_calls=3,
                current_calls=18,
            )
            out = _summary_to_str(state)

        assert "5" in out
        assert "10" in out
        assert "3" in out
        assert "18" in out

    def test_returns_string(self):
        """format_run_summary returns a string (Rich-renderable)."""
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            result = format_run_summary(RunState())
        assert isinstance(result, str)

    def test_features_merged_shown(self):
        """Features merged count appears in the summary."""
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            state = RunState(features_done=5)
            out = _summary_to_str(state)
        assert "5" in out

    def test_stuck_tasks_shown(self):
        """Stuck tasks count appears in the summary."""
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            state = RunState(stuck_tasks=2)
            out = _summary_to_str(state)
        assert "2" in out

    def test_squad_name_shown(self):
        """Squad name appears in the summary."""
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            state = RunState(squad_name="default")
            out = _summary_to_str(state)
        assert "default" in out

    def test_backend_shown(self):
        """Backend name appears in the summary."""
        with patch("orc.cli.tui.run_tui.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            state = RunState(backend="claude")
            out = _summary_to_str(state)
        assert "claude" in out


class TestRunStatePerRoleFields:
    """Verify RunState has the per-role counter fields."""

    def test_default_values_are_zero(self):
        state = RunState()
        assert state.planner_calls == 0
        assert state.coder_calls == 0
        assert state.qa_calls == 0

    def test_fields_can_be_set(self):
        state = RunState(planner_calls=1, coder_calls=2, qa_calls=3)
        assert state.planner_calls == 1
        assert state.coder_calls == 2
        assert state.qa_calls == 3

    def test_fields_are_mutable(self):
        state = RunState()
        state.planner_calls += 1
        state.coder_calls += 5
        state.qa_calls += 3
        assert state.planner_calls == 1
        assert state.coder_calls == 5
        assert state.qa_calls == 3
