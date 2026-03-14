"""Full-screen Textual TUI for `orc status`.

Two tabs navigated with ← / →:

1. **Agents** — the existing status text output (scrollable).
2. **Git Tree** — key orc-driven commits across main, dev, and all
   coder-owned feature branches, with state-machine transition commits
   highlighted.

Usage::

    from orc.cli.tui.status_tui import run_status_tui
    run_status_tui(squad="default")
"""

from __future__ import annotations

import io
import subprocess
import sys
from dataclasses import dataclass

import rich.table
from rich.console import RenderableType
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import ContentSwitcher, Static

import orc.config as _cfg
import orc.git.core as _git
from orc.coordination.client import BoardSnapshot, get_board_snapshot

# Maximum commits fetched per branch.
_MAX_COMMITS = 100

# ---------------------------------------------------------------------------
# State-machine transition detection
# ---------------------------------------------------------------------------

# Each entry: (lower-case prefix to match, rich style, icon)
_TRANSITION_RULES: list[tuple[str, str, str]] = [
    ("qa(passed)", "bold green", "✅"),
    ("qa(", "bold red", "❌"),
    ("merge feat/", "bold blue", "🔀"),
    ("chore(orc): close task", "bold cyan", "📋"),
]


def _classify_commit(subject: str) -> tuple[str, str]:
    """Return ``(rich_style, icon)`` for a commit subject, or ``("", "")``."""
    lower = subject.lower()
    for prefix, style, icon in _TRANSITION_RULES:
        if lower.startswith(prefix):
            return style, icon
    return "", ""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class CommitInfo:
    """One commit in the unified git-tree timeline."""

    sha: str
    short: str
    subject: str
    timestamp: int  # unix epoch — used for sorting
    branch: str  # branch name the commit "belongs to"
    col: int  # column index in the display table


# ---------------------------------------------------------------------------
# Git data gathering
# ---------------------------------------------------------------------------


def _main_branch() -> str:
    """Return the main branch name from config or auto-detect via git."""
    cfg_data = _cfg.load_orc_config(_cfg.get().orc_dir)
    configured = cfg_data.get("orc-main-branch", "").strip()
    if configured:
        return configured
    return _git._default_branch()


def _git_log(branch: str, exclude: list[str]) -> list[tuple[str, str, str, int]]:
    """Run ``git log`` and return ``(sha, short_sha, subject, unix_ts)`` tuples."""
    args = [
        "git",
        "log",
        "--first-parent",
        "--format=%H|%h|%s|%at",
        branch,
        *[f"^{b}" for b in exclude],
        "-n",
        str(_MAX_COMMITS),
    ]
    try:
        result = subprocess.run(args, cwd=_cfg.get().repo_root, capture_output=True, text=True)
    except Exception:
        return []

    rows: list[tuple[str, str, str, int]] = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4:
            sha, short, subject, ts_str = parts
            try:
                ts = int(ts_str)
            except ValueError:
                ts = 0
            rows.append((sha, short, subject, ts))
    return rows


def _feat_branches() -> list[str]:
    """Return sorted list of coder-owned ``feat/*`` branches (prefix-aware)."""
    prefix = _cfg.get().branch_prefix
    pattern = f"{prefix}/feat/*" if prefix else "feat/*"
    try:
        result = subprocess.run(
            ["git", "branch", "--list", pattern],
            cwd=_cfg.get().repo_root,
            capture_output=True,
            text=True,
        )
    except Exception:
        return []
    branches = [line.strip().lstrip("*+ ") for line in result.stdout.splitlines() if line.strip()]
    return sorted(branches)


def gather_git_tree() -> tuple[list[str], list[CommitInfo]]:
    """Return ``(branch_names, commits)`` for the git-tree view.

    *branch_names* is ordered ``[main, dev, feat/NNNN-a, feat/NNNN-b, …]``.
    *commits* is sorted by timestamp descending (newest first).

    Each commit is assigned to exactly one branch column:

    * main column  — commits reachable from main.
    * dev column   — commits on dev but not main.
    * feat columns — commits on feat/X but not dev or main.
    """
    try:
        main = _main_branch()
    except Exception:
        main = "main"

    dev = _cfg.get().work_dev_branch
    feats = _feat_branches()
    branches = [main, dev] + feats
    col_map = {b: i for i, b in enumerate(branches)}

    commits: list[CommitInfo] = []
    seen: set[str] = set()

    def _add(branch: str, exclude: list[str]) -> None:
        col = col_map[branch]
        for sha, short, subject, ts in _git_log(branch, exclude):
            if sha not in seen:
                seen.add(sha)
                commits.append(CommitInfo(sha, short, subject, ts, branch, col))

    _add(main, [])
    _add(dev, [main])
    for feat in feats:
        _add(feat, [dev, main])

    commits.sort(key=lambda c: c.timestamp, reverse=True)
    commits = commits[:_MAX_COMMITS]
    return branches, commits


def render_git_tree() -> RenderableType:
    """Build a Rich table showing the unified git-tree commit timeline."""
    try:
        branches, commits = gather_git_tree()
    except Exception as exc:
        return Text(f"Error building git tree: {exc}", style="bold red")

    if not branches:
        return Text("No branches found.", style="dim")

    _MAX_COL = 20  # max chars in a header name

    def _header(b: str) -> str:
        return b if len(b) <= _MAX_COL else "…" + b[-(_MAX_COL - 1) :]

    table = rich.table.Table(
        show_header=True,
        header_style="bold",
        show_lines=False,
        box=None,
        padding=(0, 1),
    )
    for b in branches:
        table.add_column(_header(b), no_wrap=True, min_width=18)

    ncols = len(branches)
    _CELL_WIDTH = 40  # max displayed chars per commit cell

    for commit in commits:
        style, icon = _classify_commit(commit.subject)
        label = (
            f"{commit.short} {icon}{commit.subject}" if icon else f"{commit.short} {commit.subject}"
        )
        if len(label) > _CELL_WIDTH:
            label = label[: _CELL_WIDTH - 1] + "…"
        cell: Text | str = Text(label, style=style) if style else label
        cells: list[Text | str] = [""] * ncols
        cells[commit.col] = cell
        table.add_row(*cells)

    if not commits:
        table.add_row(*["(no commits)" if i == 0 else "" for i in range(ncols)])

    return table


def _render_board() -> RenderableType:
    """Build a Rich kanban table from the coordination API.

    Returns a plain :class:`~rich.text.Text` message when the coordination
    server is unreachable or ``ORC_API_SOCKET`` is not set.
    """
    snap: BoardSnapshot | None = get_board_snapshot()
    if snap is None:
        return Text("no work in progress (orc server down or unreachable)")

    # -- build per-column entry lists ----------------------------------
    to_refine = snap.visions

    planned = [t["name"] for t in snap.tasks if t.get("status") == "planned"]

    _IN_PROGRESS_STATUSES = {"coding", "blocked", "soft-blocked"}
    in_progress_entries = []
    for t in snap.tasks:
        if t.get("status") in _IN_PROGRESS_STATUSES:
            label = t["name"]
            if t.get("assigned_to"):
                label = f"{label} ({t['assigned_to']})"
            in_progress_entries.append(label)

    review_entries = []
    for t in snap.tasks:
        if t.get("status") == "review":
            label = t["name"]
            if t.get("branch"):
                label = f"{label} ({t['branch']})"
            review_entries.append(label)

    done_entries = [t["name"] for t in snap.done]

    def _cell(entries: list[str]) -> str:
        return "\n".join(entries) if entries else "(empty)"

    table = rich.table.Table(
        show_header=True,
        header_style="bold",
        show_lines=True,
        padding=(0, 1),
    )
    for col_name in ("To refine", "To do", "In progress", "Awaiting review", "Done"):
        table.add_column(col_name, no_wrap=False)

    table.add_row(
        _cell(to_refine),
        _cell(planned),
        _cell(in_progress_entries),
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

_TAB_NAMES = ["Agents", "Git Tree", "Board"]
_TAB_IDS = ["tab-agents", "tab-git-tree", "tab-board"]

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
        self._git_tree_loaded = False
        self._board_loaded = False

    def compose(self) -> ComposeResult:
        yield Static(self._tab_bar_markup(), id="tab-bar")
        with ContentSwitcher(initial=_TAB_IDS[0]):  # pragma: no cover
            with VerticalScroll(id=_TAB_IDS[0]):
                yield Static("Loading…", id="agents-content")
            with VerticalScroll(id=_TAB_IDS[1]):
                yield Static("Press → to load git tree…", id="git-tree-content")
            with VerticalScroll(id=_TAB_IDS[2]):
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
        if self._tab_index == 1 and not self._git_tree_loaded:
            self._git_tree_loaded = True
            self._refresh_git_tree()
        if self._tab_index == 2 and not self._board_loaded:
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
        if self._tab_index == 2:
            scroll = self._active_scroll()
            if scroll is not None:
                scroll.scroll_left()

    def action_scroll_right_board(self) -> None:
        """Scroll the Board tab horizontally to the right."""
        if self._tab_index == 2:
            scroll = self._active_scroll()
            if scroll is not None:
                scroll.scroll_right()

    # ------------------------------------------------------------------
    # Content loaders
    # ------------------------------------------------------------------

    def _refresh_agents(self) -> None:
        text = _capture_status(self._squad)
        self.query_one("#agents-content", Static).update(text)

    def _refresh_git_tree(self) -> None:
        renderable = render_git_tree()
        self.query_one("#git-tree-content", Static).update(renderable)

    def _refresh_board(self) -> None:
        renderable = _render_board()
        self.query_one("#board-content", Static).update(renderable)


def run_status_tui(squad: str = "default") -> None:
    """Launch the interactive ``orc status`` TUI."""
    StatusApp(squad=squad).run()
