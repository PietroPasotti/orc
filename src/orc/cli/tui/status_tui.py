"""Full-screen Textual TUI for `orc status`.

Two tabs navigated with ← / →:

1. **Agents** — the existing status text output (scrollable).
2. **Board** — kanban board from the coordination API.

Usage::

    from orc.cli.tui.status_tui import run_status_tui
    run_status_tui(squad="default")
"""

from __future__ import annotations

import io
import sys

import rich.table
from rich.console import RenderableType
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import ContentSwitcher, Static

from orc.coordination.board import TaskStatus
from orc.coordination.client import BoardSnapshot, get_board_snapshot


def _render_board() -> RenderableType:
    """Build a Rich kanban table from the coordination API.

    Returns a plain :class:`~rich.text.Text` message when the coordination
    server is unreachable.
    """
    snap: BoardSnapshot | None = get_board_snapshot()
    if snap is None:
        return Text("no work in progress (orc server down or unreachable)")

    # -- build per-column entry lists ----------------------------------
    to_refine = snap.visions

    planned = [t.name for t in snap.tasks if t.status == TaskStatus.PLANNED]

    _IN_PROGRESS_STATUSES = {TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED}
    in_progress_entries = []
    for t in snap.tasks:
        if t.status in _IN_PROGRESS_STATUSES:
            label = t.name
            if t.assigned_to:
                label = f"{label} ({t.assigned_to})"
            in_progress_entries.append(label)

    stuck_entries = [t.name for t in snap.tasks if t.status == TaskStatus.STUCK]

    review_entries = []
    for t in snap.tasks:
        if t.status == TaskStatus.IN_REVIEW:
            label = t.name
            branch = getattr(t, "branch", None)
            if branch:
                label = f"{label} ({branch})"
            review_entries.append(label)

    done_entries = [t.name for t in snap.tasks if t.status == TaskStatus.DONE]

    def _cell(entries: list[str]) -> str:
        return "\n".join(entries) if entries else "(empty)"

    table = rich.table.Table(
        show_header=True,
        header_style="bold",
        show_lines=True,
        padding=(0, 1),
    )
    for col_name in ("To refine", "To do", "In progress", "🔧 Stuck", "Awaiting review", "Done"):
        table.add_column(col_name, no_wrap=False)

    table.add_row(
        _cell(to_refine),
        _cell(planned),
        _cell(in_progress_entries),
        _cell(stuck_entries),
        _cell(review_entries),
        _cell(done_entries),
    )

    return table


# ---------------------------------------------------------------------------
# Agent view — capture _status() text output
# ---------------------------------------------------------------------------


def _capture_status(squad: str = "default") -> str:
    """Run ``_status()`` and capture its ``typer.echo`` output as plain text."""
    # Late import to avoid circular dependency (status.py imports from this file).
    from orc.cli.status import _status  # noqa: PLC0415

    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        _status(squad=squad)
    except Exception as exc:
        return f"Error fetching status: {exc}"
    finally:
        sys.stdout = old_stdout
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Textual app
# ---------------------------------------------------------------------------

_TAB_NAMES = ["Agents", "Board"]
_TAB_IDS = ["tab-agents", "tab-board"]

_CSS = """\
#tab-bar {
    height: 1;
    background: $surface;
    padding: 0 1;
    dock: top;
}
ContentSwitcher {
    height: 1fr;
}
VerticalScroll {
    height: 1fr;
}
"""


class StatusApp(App[None]):
    """Full-screen Textual TUI for ``orc status``."""

    CSS = _CSS

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("left", "tab_prev", "← Prev", priority=True),
        Binding("right", "tab_next", "Next →", priority=True),
        Binding("j", "scroll_down_content", "Scroll ↓", show=False),
        Binding("k", "scroll_up_content", "Scroll ↑", show=False),
        Binding("[", "scroll_left_board", "← Board", show=False),
        Binding("]", "scroll_right_board", "→ Board", show=False),
    ]

    def __init__(self, squad: str = "default") -> None:
        super().__init__()
        self._squad = squad
        self._tab_index = 0
        self._board_loaded = False

    def compose(self) -> ComposeResult:
        yield Static(self._tab_bar_markup(), id="tab-bar")
        with ContentSwitcher(initial=_TAB_IDS[0]):  # pragma: no cover
            with VerticalScroll(id=_TAB_IDS[0]):
                yield Static("Loading…", id="agents-content")
            with VerticalScroll(id=_TAB_IDS[1]):
                yield Static("Press → to load board…", id="board-content")

    def on_mount(self) -> None:
        self._refresh_agents()

    # ------------------------------------------------------------------
    # Tab navigation
    # ------------------------------------------------------------------

    def action_tab_prev(self) -> None:
        self._tab_index = (self._tab_index - 1) % len(_TAB_NAMES)
        self._apply_tab()

    def action_tab_next(self) -> None:
        self._tab_index = (self._tab_index + 1) % len(_TAB_NAMES)
        self._apply_tab()

    def _apply_tab(self) -> None:
        self.query_one("#tab-bar", Static).update(self._tab_bar_markup())
        self.query_one(ContentSwitcher).current = _TAB_IDS[self._tab_index]
        if self._tab_index == 1 and not self._board_loaded:
            self._board_loaded = True
            self._refresh_board()

    def _tab_bar_markup(self) -> str:
        parts: list[str] = []
        for i, name in enumerate(_TAB_NAMES):
            if i == self._tab_index:
                parts.append(f"[reverse bold] {name} [/reverse bold]")
            else:
                parts.append(f"[dim] {name} [/dim]")
        hint = "  [dim]← / → switch tabs · j/k or ↑/↓ scroll · [ ] scroll board · q quit[/dim]"
        return "  ".join(parts) + hint

    # ------------------------------------------------------------------
    # Scroll helpers
    # ------------------------------------------------------------------

    def _active_scroll(self) -> VerticalScroll | None:
        try:
            return self.query_one(f"#{_TAB_IDS[self._tab_index]}", VerticalScroll)
        except Exception:
            return None

    def action_scroll_down_content(self) -> None:
        scroll = self._active_scroll()
        if scroll is not None:
            scroll.scroll_down()

    def action_scroll_up_content(self) -> None:
        scroll = self._active_scroll()
        if scroll is not None:
            scroll.scroll_up()

    def action_scroll_left_board(self) -> None:
        """Scroll the Board tab horizontally to the left."""
        if self._tab_index == 1:
            scroll = self._active_scroll()
            if scroll is not None:
                scroll.scroll_left()

    def action_scroll_right_board(self) -> None:
        """Scroll the Board tab horizontally to the right."""
        if self._tab_index == 1:
            scroll = self._active_scroll()
            if scroll is not None:
                scroll.scroll_right()

    # ------------------------------------------------------------------
    # Content loaders
    # ------------------------------------------------------------------

    def _refresh_agents(self) -> None:
        text = _capture_status(self._squad)
        self.query_one("#agents-content", Static).update(text)

    def _refresh_board(self) -> None:
        renderable = _render_board()
        self.query_one("#board-content", Static).update(renderable)


def run_status_tui(squad: str = "default") -> None:
    """Launch the interactive ``orc status`` TUI."""
    StatusApp(squad=squad).run()
