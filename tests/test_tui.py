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
    render,
    run_tui,
)
from orc.engine.dispatcher import DispatcherPhase
from orc.squad import AgentRole


def _row(
    *,
    agent_id: str = "coder-1",
    role: AgentRole = "coder",
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


def _render_to_str(state: RunState, error: bool = False, elapsed_seconds: int = 0) -> str:
    buf = io.StringIO()
    console = rich.console.Console(file=buf, width=120, highlight=False)
    console.print(state.rich_summary(error, elapsed_seconds))
    return buf.getvalue()


def _render_tui_to_str(state: RunState) -> str:
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
        state = RunState(dispatcher_phase=DispatcherPhase.DRAINING)
        out = _render_tui_to_str(state)
        assert "⏳ draining…" in out

    def test_header_excludes_draining_when_inactive(self):
        state = RunState(dispatcher_phase=DispatcherPhase.RUNNING)
        out = _render_tui_to_str(state)
        assert "draining" not in out

    def test_draining_default_is_false(self):
        state = RunState()
        assert state.draining is False

    def test_draining_derived_from_dispatcher_phase(self):
        """draining property reflects dispatcher_phase."""
        state = RunState()
        assert state.draining is False
        state.dispatcher_phase = DispatcherPhase.DRAINING
        assert state.draining is True


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


class TestRunStateRichPrint:
    """Verify RunState's pprint method."""

    def test_summary(self):
        state = RunState(
            current_calls=3,
            max_calls=10,
            backend="openai",
            telegram_ok=True,
            squad_repr="broad (1-4-1)",
            run_started_at=60.0,
            features_done=5,
        )
        _render_to_str(state)


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
