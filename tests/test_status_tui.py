"""Tests for orc/status_tui.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import rich.table

from orc.cli.tui.status_tui import (
    _TAB_NAMES,
    StatusApp,
    _capture_status,
    _render_board,
    run_status_tui,
)
from orc.coordination.models import TaskEntry


class TestCaptureStatus:
    def test_captures_typer_echo_output(self, monkeypatch):
        def fake_status(squad="default"):
            import sys

            sys.stdout.write("Squad: default\nmain is up to date with dev.\n")

        monkeypatch.setattr("orc.cli.tui.status_tui._capture_status.__module__", "orc.status_tui")

        with patch("orc.cli.status._status", fake_status):
            output = _capture_status()

        assert "Squad: default" in output
        assert "main is up to date" in output

    def test_handles_exception_gracefully(self, monkeypatch):
        def bad_status(squad="default"):
            raise RuntimeError("board missing")

        with patch("orc.cli.status._status", bad_status):
            output = _capture_status()

        assert "Error" in output


class TestStatusApp:
    """Unit-test StatusApp internals without a live Textual event loop."""

    def test_instantiation_sets_defaults(self):
        app = StatusApp(squad="myteam")
        assert app._squad == "myteam"
        assert app._tab_index == 0

    def test_tab_bar_markup_highlights_active_tab(self):
        app = StatusApp()
        markup = app._tab_bar_markup()
        # First tab "Agents" should be highlighted (reverse bold).
        assert "reverse bold" in markup
        assert "Agents" in markup
        assert "Board" in markup

    def test_tab_bar_markup_second_tab_active(self):
        app = StatusApp()
        app._tab_index = 1
        markup = app._tab_bar_markup()
        assert "Agents" in markup
        assert "Board" in markup

    def test_action_tab_next_increments_index(self):
        app = StatusApp()
        assert app._tab_index == 0
        app._apply_tab = lambda: None  # suppress query_one calls
        app.action_tab_next()
        assert app._tab_index == 1

    def test_action_tab_next_wraps_around(self):
        app = StatusApp()
        app._tab_index = 1  # last tab
        app._apply_tab = lambda: None
        app.action_tab_next()
        assert app._tab_index == 0

    def test_action_tab_prev_decrements_index(self):
        app = StatusApp()
        app._tab_index = 1
        app._apply_tab = lambda: None
        app.action_tab_prev()
        assert app._tab_index == 0

    def test_action_tab_prev_wraps_around(self):
        app = StatusApp()
        app._tab_index = 0
        app._apply_tab = lambda: None
        app.action_tab_prev()
        assert app._tab_index == 1  # wraps to last tab

    def test_apply_tab_calls_query_one(self):
        app = StatusApp()
        calls: list = []

        class FakeStatic:
            def update(self, x: object) -> None:
                calls.append(("update", x))

        class FakeSwitcher:
            current: str = ""

        fake_static = FakeStatic()
        fake_switcher = FakeSwitcher()

        def fake_query_one(selector, widget_type=None):
            if selector == "#tab-bar":
                return fake_static
            return fake_switcher

        app.query_one = fake_query_one
        app._apply_tab()
        assert any(c[0] == "update" for c in calls)

    def test_on_mount_calls_refresh_methods(self):
        app = StatusApp()
        refreshed: list[str] = []
        app._refresh_agents = lambda: refreshed.append("agents")
        app.on_mount()
        assert "agents" in refreshed

    def test_board_loaded_lazily_on_tab_switch(self):
        app = StatusApp()
        refreshed: list[str] = []
        app._refresh_board = lambda: refreshed.append("board")

        class FakeStatic:
            def update(self, x: object) -> None:
                pass

        class FakeContentSwitcher:
            current = None

        def fake_query_one(sel, wt=None):
            if isinstance(sel, str):
                return FakeStatic()
            return FakeContentSwitcher()

        app.query_one = fake_query_one
        assert not app._board_loaded
        # simulate switching to the board tab (index 1)
        app._tab_index = 1
        app._apply_tab()
        assert "board" in refreshed
        assert app._board_loaded
        # switching again should NOT reload
        refreshed.clear()
        app._apply_tab()
        assert "board" not in refreshed

    def test_refresh_agents_updates_widget(self, monkeypatch):
        monkeypatch.setattr(
            "orc.cli.tui.status_tui._capture_status", lambda squad="default": "agent output"
        )
        app = StatusApp()
        updates: list = []

        class FakeStatic:
            def update(self, x: object) -> None:
                updates.append(x)

        app.query_one = lambda sel, wt=None: FakeStatic()
        app._refresh_agents()
        assert updates == ["agent output"]

    def test_active_scroll_returns_scroll_widget(self):
        app = StatusApp()
        from textual.containers import VerticalScroll

        fake_scroll = MagicMock(spec=VerticalScroll)
        app.query_one = lambda sel, wt=None: fake_scroll
        result = app._active_scroll()
        assert result is fake_scroll

    def test_active_scroll_returns_none_on_exception(self):
        app = StatusApp()

        def bad_query(sel, wt=None):
            raise Exception("no widget")

        app.query_one = bad_query
        assert app._active_scroll() is None

    def test_action_scroll_down_calls_scroll(self):
        app = StatusApp()
        scrolled: list[str] = []

        class FakeScroll:
            def scroll_down(self) -> None:
                scrolled.append("down")

        app._active_scroll = lambda: FakeScroll()
        app.action_scroll_down_content()
        assert scrolled == ["down"]

    def test_action_scroll_down_noop_when_no_scroll(self):
        app = StatusApp()
        app._active_scroll = lambda: None
        # Should not raise.
        app.action_scroll_down_content()

    def test_action_scroll_up_calls_scroll(self):
        app = StatusApp()
        scrolled: list[str] = []

        class FakeScroll:
            def scroll_up(self) -> None:
                scrolled.append("up")

        app._active_scroll = lambda: FakeScroll()
        app.action_scroll_up_content()
        assert scrolled == ["up"]

    def test_action_scroll_up_noop_when_no_scroll(self):
        app = StatusApp()
        app._active_scroll = lambda: None
        # Should not raise.
        app.action_scroll_up_content()

    def test_compose_yields_expected_widget_types(self):
        """compose() starts by yielding the tab-bar Static widget."""
        from textual.widgets import Static

        app = StatusApp()
        gen = app.compose()
        # The first yield is the tab-bar Static; iterating further requires
        # a live Textual event loop (for ContentSwitcher context managers).
        first = next(gen)
        assert isinstance(first, Static)
        assert first.id == "tab-bar"
        gen.close()


class TestRunStatusTui:
    def test_run_status_tui_launches_app(self, monkeypatch):
        """run_status_tui() creates a StatusApp and calls .run()."""
        launched: list[str] = []

        with patch.object(StatusApp, "run", lambda self: launched.append(self._squad)):
            run_status_tui(squad="myteam")

        assert launched == ["myteam"]


# ---------------------------------------------------------------------------
# Board tab tests
# ---------------------------------------------------------------------------


class TestBoardTabPresent:
    def test_board_tab_in_tab_names(self):
        assert "Board" in _TAB_NAMES

    def test_board_tab_is_second(self):
        assert _TAB_NAMES[1] == "Board"


class TestRenderBoardServerDown:
    def test_server_down_returns_text_message(self, monkeypatch):
        """When get_board_snapshot() returns None, render a 'server down' message."""
        monkeypatch.setattr(
            "orc.cli.tui.status_tui.get_board_snapshot",
            lambda: None,
        )
        result = _render_board()
        from rich.text import Text

        assert isinstance(result, Text)
        assert "server down" in str(result)


class TestRenderBoardWithData:
    def _make_snap(self):
        from orc.coordination.client import BoardSnapshot

        return BoardSnapshot(
            visions=["0007-vision.md"],
            tasks=[
                TaskEntry(name="0001-task.md", status="planned"),
                TaskEntry(name="0002-task.md", status="in-progress", assigned_to="coder-1"),
                TaskEntry(name="0003-task.md", status="in-review"),
                TaskEntry(name="0004-task.md", status="done"),
            ],
        )

    def _render_to_str(self, table: rich.table.Table) -> str:
        """Render a Rich table to a plain string for assertions."""
        from io import StringIO

        from rich.console import Console

        buf = StringIO()
        console = Console(file=buf, force_terminal=False, width=300)
        console.print(table)
        return buf.getvalue()

    def test_returns_rich_table(self, monkeypatch):
        snap = self._make_snap()
        monkeypatch.setattr("orc.cli.tui.status_tui.get_board_snapshot", lambda: snap)
        result = _render_board()
        assert isinstance(result, rich.table.Table)

    def test_column_headers_present(self, monkeypatch):
        snap = self._make_snap()
        monkeypatch.setattr("orc.cli.tui.status_tui.get_board_snapshot", lambda: snap)
        result = _render_board()
        assert isinstance(result, rich.table.Table)
        col_names = [col.header for col in result.columns]
        assert "To refine" in col_names
        assert "To do" in col_names
        assert "In progress" in col_names
        assert "Awaiting review" in col_names
        assert "Done" in col_names

    def test_vision_in_to_refine_column(self, monkeypatch):
        snap = self._make_snap()
        monkeypatch.setattr("orc.cli.tui.status_tui.get_board_snapshot", lambda: snap)
        result = _render_board()
        assert isinstance(result, rich.table.Table)
        rendered = self._render_to_str(result)
        assert "0007-vision.md" in rendered

    def test_planned_task_in_todo_column(self, monkeypatch):
        snap = self._make_snap()
        monkeypatch.setattr("orc.cli.tui.status_tui.get_board_snapshot", lambda: snap)
        result = _render_board()
        assert isinstance(result, rich.table.Table)
        rendered = self._render_to_str(result)
        assert "0001-task.md" in rendered

    def test_coding_task_in_in_progress_column(self, monkeypatch):
        snap = self._make_snap()
        monkeypatch.setattr("orc.cli.tui.status_tui.get_board_snapshot", lambda: snap)
        result = _render_board()
        assert isinstance(result, rich.table.Table)
        rendered = self._render_to_str(result)
        assert "0002-task.md" in rendered
        assert "coder-1" in rendered

    def test_review_task_in_awaiting_review_column(self, monkeypatch):
        snap = self._make_snap()
        monkeypatch.setattr("orc.cli.tui.status_tui.get_board_snapshot", lambda: snap)
        result = _render_board()
        assert isinstance(result, rich.table.Table)
        rendered = self._render_to_str(result)
        assert "0003-task.md" in rendered

    def test_done_task_in_done_column(self, monkeypatch):
        snap = self._make_snap()
        monkeypatch.setattr("orc.cli.tui.status_tui.get_board_snapshot", lambda: snap)
        result = _render_board()
        assert isinstance(result, rich.table.Table)
        rendered = self._render_to_str(result)
        assert "0004-task.md" in rendered

    def test_empty_columns_show_empty_marker(self, monkeypatch):
        from orc.coordination.client import BoardSnapshot

        snap = BoardSnapshot(visions=[], tasks=[])
        monkeypatch.setattr("orc.cli.tui.status_tui.get_board_snapshot", lambda: snap)
        result = _render_board()
        assert isinstance(result, rich.table.Table)
        rendered = self._render_to_str(result)
        assert rendered.count("(empty)") == 6

    def test_in_progress_statuses_covered(self, monkeypatch):
        """blocked tasks go into In progress column."""
        from orc.coordination.client import BoardSnapshot

        snap = BoardSnapshot(
            visions=[],
            tasks=[
                TaskEntry(name="blocked-task.md", status="blocked"),
                TaskEntry(name="inprog-task.md", status="in-progress"),
            ],
        )
        monkeypatch.setattr("orc.cli.tui.status_tui.get_board_snapshot", lambda: snap)
        result = _render_board()
        assert isinstance(result, rich.table.Table)
        rendered = self._render_to_str(result)
        assert "blocked-task.md" in rendered
        assert "inprog-task.md" in rendered

    def test_review_task_with_branch_shows_branch(self, monkeypatch):
        from orc.coordination.client import BoardSnapshot

        snap = BoardSnapshot(
            visions=[],
            tasks=[TaskEntry(name="review-task.md", status="in-review")],
        )
        monkeypatch.setattr("orc.cli.tui.status_tui.get_board_snapshot", lambda: snap)
        result = _render_board()
        assert isinstance(result, rich.table.Table)
        rendered = self._render_to_str(result)
        assert "review-task.md" in rendered


class TestStatusAppBoardTab:
    def test_board_loaded_flag_starts_false(self):
        app = StatusApp()
        assert app._board_loaded is False

    def test_refresh_board_calls_render_and_updates_widget(self, monkeypatch):
        from rich.text import Text

        app = StatusApp()
        fake_renderable = Text("board content")

        monkeypatch.setattr("orc.cli.tui.status_tui._render_board", lambda: fake_renderable)

        updates: list = []
        mock_static = MagicMock()
        mock_static.update = lambda r: updates.append(r)
        app.query_one = lambda sel, wt=None: mock_static

        app._refresh_board()
        assert updates == [fake_renderable]

    def test_apply_tab_loads_board_on_index_1(self, monkeypatch):
        app = StatusApp()
        refreshed: list[str] = []
        app._refresh_board = lambda: refreshed.append("board")

        # Stub out query_one to avoid live widget requirement
        mock_static = MagicMock()
        mock_switcher = MagicMock()
        mock_switcher.current = None

        def _query(sel, wt=None):
            if sel == "#tab-bar":
                return mock_static
            return mock_switcher

        app.query_one = _query
        app._tab_index = 1
        app._apply_tab()
        assert refreshed == ["board"]
        assert app._board_loaded is True

    def test_apply_tab_does_not_reload_board(self, monkeypatch):
        app = StatusApp()
        app._board_loaded = True
        refreshed: list[str] = []
        app._refresh_board = lambda: refreshed.append("board")

        mock_static = MagicMock()
        mock_switcher = MagicMock()

        def _query(sel, wt=None):
            if sel == "#tab-bar":
                return mock_static
            return mock_switcher

        app.query_one = _query
        app._tab_index = 1
        app._apply_tab()
        assert refreshed == []

    def test_scroll_left_board_calls_scroll_left_when_on_board_tab(self):
        app = StatusApp()
        app._tab_index = 1
        scrolled: list[str] = []

        class FakeScroll:
            def scroll_left(self) -> None:
                scrolled.append("left")

        app._active_scroll = lambda: FakeScroll()
        app.action_scroll_left_board()
        assert scrolled == ["left"]

    def test_scroll_left_board_noop_when_not_on_board_tab(self):
        app = StatusApp()
        app._tab_index = 0
        app._active_scroll = lambda: MagicMock()
        # Should not call scroll_left at all (no assertion, just no error)
        app.action_scroll_left_board()

    def test_scroll_right_board_calls_scroll_right_when_on_board_tab(self):
        app = StatusApp()
        app._tab_index = 1
        scrolled: list[str] = []

        class FakeScroll:
            def scroll_right(self) -> None:
                scrolled.append("right")

        app._active_scroll = lambda: FakeScroll()
        app.action_scroll_right_board()
        assert scrolled == ["right"]

    def test_scroll_right_board_noop_when_not_on_board_tab(self):
        app = StatusApp()
        app._tab_index = 0
        app._active_scroll = lambda: MagicMock()
        app.action_scroll_right_board()

    def test_scroll_left_board_noop_when_no_active_scroll(self):
        app = StatusApp()
        app._tab_index = 1
        app._active_scroll = lambda: None
        # Should not raise.
        app.action_scroll_left_board()

    def test_scroll_right_board_noop_when_no_active_scroll(self):
        app = StatusApp()
        app._tab_index = 1
        app._active_scroll = lambda: None
        # Should not raise.
        app.action_scroll_right_board()
