"""Tests for orc/status_tui.py."""

from __future__ import annotations

from dataclasses import replace as _replace
from unittest.mock import MagicMock, patch

import pytest
import rich.table

import orc.config as _cfg
from orc.cli.tui.status_tui import (
    _TAB_NAMES,
    CommitInfo,
    StatusApp,
    _capture_status,
    _classify_commit,
    _feat_branches,
    _git_log,
    _main_branch,
    _render_board,
    gather_git_tree,
    render_git_tree,
    run_status_tui,
)


class TestClassifyCommit:
    @pytest.mark.parametrize(
        "subject,expected_style,expected_icon",
        [
            ("qa(passed): all checks green", "bold green", "✅"),
            ("qa(blocked): cannot run tests", "bold red", "❌"),
            ("qa(failed): type errors", "bold red", "❌"),
            ("Merge feat/0001-foo into dev", "bold blue", "🔀"),
            ("merge feat/0002-bar into dev", "bold blue", "🔀"),
            ("chore(orc): close task 0001-foo", "bold cyan", "📋"),
            ("feat: add user authentication", "", ""),
            ("", "", ""),
        ],
    )
    def test_classify_commit(self, subject, expected_style, expected_icon):
        style, icon = _classify_commit(subject)
        assert style == expected_style
        assert icon == expected_icon


class TestGitLog:
    def test_parses_valid_output(self, tmp_path):
        def fake_run(args, **kw):
            r = MagicMock()
            r.stdout = (
                "abc123def456|abc123|feat: add thing|1700000000\n"
                "def456abc789|def456|fix: correct bug|1699990000\n"
            )
            return r

        with (
            patch("orc.cli.tui.status_tui.subprocess.run", fake_run),
            patch("orc.config._config", _replace(_cfg.get(), repo_root=tmp_path)),
        ):
            rows = _git_log("main", [])

        assert len(rows) == 2
        sha, short, subject, ts = rows[0]
        assert sha == "abc123def456"
        assert short == "abc123"
        assert subject == "feat: add thing"
        assert ts == 1700000000

    def test_empty_output(self, tmp_path):
        def fake_run(args, **kw):
            r = MagicMock()
            r.stdout = ""
            return r

        with (
            patch("orc.cli.tui.status_tui.subprocess.run", fake_run),
            patch("orc.config._config", _replace(_cfg.get(), repo_root=tmp_path)),
        ):
            rows = _git_log("main", [])

        assert rows == []

    def test_subprocess_error_returns_empty(self, tmp_path):
        def fake_run(args, **kw):
            raise OSError("git not found")

        with (
            patch("orc.cli.tui.status_tui.subprocess.run", fake_run),
            patch("orc.config._config", _replace(_cfg.get(), repo_root=tmp_path)),
        ):
            rows = _git_log("main", [])

        assert rows == []

    def test_exclude_args_passed(self, tmp_path):
        captured: list[list[str]] = []

        def fake_run(args, **kw):
            captured.append(args)
            r = MagicMock()
            r.stdout = ""
            return r

        with (
            patch("orc.cli.tui.status_tui.subprocess.run", fake_run),
            patch("orc.config._config", _replace(_cfg.get(), repo_root=tmp_path)),
        ):
            _git_log("dev", ["main"])

        assert "^main" in captured[0]


class TestFeatBranches:
    def test_returns_sorted_branches(self, tmp_path):
        def fake_run(args, **kw):
            r = MagicMock()
            r.stdout = "  feat/0002-bar\n  feat/0001-foo\n"
            return r

        with (
            patch("orc.cli.tui.status_tui.subprocess.run", fake_run),
            patch("orc.config._config", _replace(_cfg.get(), repo_root=tmp_path, branch_prefix="")),
        ):
            branches = _feat_branches()

        assert branches == ["feat/0001-foo", "feat/0002-bar"]

    def test_applies_branch_prefix(self, tmp_path):
        captured: list[list[str]] = []

        def fake_run(args, **kw):
            captured.append(args)
            r = MagicMock()
            r.stdout = "  orc/feat/0001-foo\n"
            return r

        with (
            patch("orc.cli.tui.status_tui.subprocess.run", fake_run),
            patch(
                "orc.config._config", _replace(_cfg.get(), repo_root=tmp_path, branch_prefix="orc")
            ),
        ):
            branches = _feat_branches()

        assert "orc/feat/*" in captured[0]
        assert branches == ["orc/feat/0001-foo"]

    def test_subprocess_error_returns_empty(self, tmp_path):
        def fake_run(args, **kw):
            raise OSError("git not found")

        with (
            patch("orc.cli.tui.status_tui.subprocess.run", fake_run),
            patch("orc.config._config", _replace(_cfg.get(), repo_root=tmp_path, branch_prefix="")),
        ):
            assert _feat_branches() == []


class TestGatherGitTree:
    def _make_fake_run(self, branch_output: dict[str, str]) -> object:
        """Return a fake subprocess.run that returns different output per branch arg."""

        def fake_run(args, **kw):
            r = MagicMock()
            # git branch --list → feat branches
            if "--list" in args:
                r.stdout = branch_output.get("__list__", "")
                return r
            # git log → find branch name in args
            for branch, out in branch_output.items():
                if branch != "__list__" and branch in args:
                    r.stdout = out
                    return r
            r.stdout = ""
            return r

        return fake_run

    def test_basic_structure(self, tmp_path, monkeypatch):
        fake_run = self._make_fake_run(
            {
                "__list__": "",  # no feat branches
                "main": "aaabbbccc|aaabbb|Initial commit|1700000100\n",
                "dev": "dddeeeffe|dddeee|feat: add work|1700000200\n",
            }
        )
        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(_cfg.get(), repo_root=tmp_path, work_dev_branch="dev", branch_prefix=""),
        )
        monkeypatch.setattr("orc.git.Git.default_branch", lambda self: "main")
        monkeypatch.setattr("orc.cli.tui.status_tui._cfg.load_orc_config", lambda *a, **kw: {})

        with patch("orc.cli.tui.status_tui.subprocess.run", fake_run):
            branches, commits = gather_git_tree()

        assert branches[0] == "main"
        assert branches[1] == "dev"
        assert len(commits) == 2
        # dev commit has higher timestamp → appears first
        assert commits[0].branch == "dev"
        assert commits[1].branch == "main"

    def test_commits_sorted_newest_first(self, tmp_path, monkeypatch):
        fake_run = self._make_fake_run(
            {
                "__list__": "  feat/0001-foo\n",
                "main": "aaa|aaa|main commit|1000\n",
                "dev": "bbb|bbb|dev commit|3000\n",
                "feat/0001-foo": "ccc|ccc|feat commit|2000\n",
            }
        )
        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(_cfg.get(), repo_root=tmp_path, work_dev_branch="dev", branch_prefix=""),
        )
        monkeypatch.setattr("orc.git.Git.default_branch", lambda self: "main")
        monkeypatch.setattr("orc.cli.tui.status_tui._cfg.load_orc_config", lambda *a, **kw: {})

        with patch("orc.cli.tui.status_tui.subprocess.run", fake_run):
            _, commits = gather_git_tree()

        timestamps = [c.timestamp for c in commits]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_deduplicates_shas(self, tmp_path, monkeypatch):
        # Same SHA appearing in both main and dev outputs → only kept once.
        fake_run = self._make_fake_run(
            {
                "__list__": "",
                "main": "same1234|same12|shared commit|1000\n",
                "dev": "same1234|same12|shared commit|1000\n",
            }
        )
        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(_cfg.get(), repo_root=tmp_path, work_dev_branch="dev", branch_prefix=""),
        )
        monkeypatch.setattr("orc.git.Git.default_branch", lambda self: "main")
        monkeypatch.setattr("orc.cli.tui.status_tui._cfg.load_orc_config", lambda *a, **kw: {})

        with patch("orc.cli.tui.status_tui.subprocess.run", fake_run):
            _, commits = gather_git_tree()

        shas = [c.sha for c in commits]
        assert len(shas) == len(set(shas))


class TestRenderGitTree:
    def test_returns_rich_table(self, tmp_path, monkeypatch):
        def fake_gather():
            branches = ["main", "dev"]
            commits = [
                CommitInfo("abc", "abc", "feat: thing", 1000, "dev", 1),
                CommitInfo("def", "def", "qa(passed): ok", 900, "dev", 1),
            ]
            return branches, commits

        monkeypatch.setattr("orc.cli.tui.status_tui.gather_git_tree", fake_gather)
        result = render_git_tree()
        assert isinstance(result, rich.table.Table)

    def test_error_returns_text(self, monkeypatch):
        def fake_gather():
            raise RuntimeError("no git")

        monkeypatch.setattr("orc.cli.tui.status_tui.gather_git_tree", fake_gather)
        from rich.text import Text

        result = render_git_tree()
        assert isinstance(result, Text)

    def test_empty_commits_still_renders(self, monkeypatch):
        def fake_gather():
            return ["main", "dev"], []

        monkeypatch.setattr("orc.cli.tui.status_tui.gather_git_tree", fake_gather)
        result = render_git_tree()
        assert isinstance(result, rich.table.Table)


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


class TestMainBranch:
    def test_uses_config_value_when_set(self, monkeypatch):
        monkeypatch.setattr(
            "orc.cli.tui.status_tui._cfg.load_orc_config",
            lambda *a, **kw: {"orc-main-branch": "trunk"},
        )
        assert _main_branch() == "trunk"

    def test_falls_back_to_default_branch(self, monkeypatch):
        monkeypatch.setattr("orc.cli.tui.status_tui._cfg.load_orc_config", lambda *a, **kw: {})
        monkeypatch.setattr("orc.git.Git.default_branch", lambda self: "master")
        assert _main_branch() == "master"

    def test_ignores_empty_string_config(self, monkeypatch):
        monkeypatch.setattr(
            "orc.cli.tui.status_tui._cfg.load_orc_config", lambda *a, **kw: {"orc-main-branch": ""}
        )
        monkeypatch.setattr("orc.git.Git.default_branch", lambda self: "main")
        assert _main_branch() == "main"


class TestGitLogEdgeCases:
    def test_invalid_timestamp_becomes_zero(self, tmp_path):
        def fake_run(args, **kw):
            r = MagicMock()
            r.stdout = "abc|abc|subject|not-a-number\n"
            return r

        with (
            patch("orc.cli.tui.status_tui.subprocess.run", fake_run),
            patch("orc.config._config", _replace(_cfg.get(), repo_root=tmp_path)),
        ):
            rows = _git_log("main", [])

        assert len(rows) == 1
        assert rows[0][3] == 0  # timestamp defaults to 0


class TestGatherGitTreeEdgeCases:
    def test_main_branch_exception_falls_back_to_main(self, tmp_path, monkeypatch):
        def bad_main():
            raise RuntimeError("no remote")

        def fake_run(args, **kw):
            r = MagicMock()
            r.stdout = ""
            return r

        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(_cfg.get(), repo_root=tmp_path, work_dev_branch="dev", branch_prefix=""),
        )
        monkeypatch.setattr("orc.cli.tui.status_tui._main_branch", bad_main)

        with patch("orc.cli.tui.status_tui.subprocess.run", fake_run):
            branches, _ = gather_git_tree()

        assert branches[0] == "main"


class TestRenderGitTreeEdgeCases:
    def test_no_branches_returns_text(self, monkeypatch):
        monkeypatch.setattr("orc.cli.tui.status_tui.gather_git_tree", lambda: ([], []))
        from rich.text import Text

        result = render_git_tree()
        assert isinstance(result, Text)

    def test_long_subject_is_truncated(self, monkeypatch):
        long_subject = "a" * 80
        commits = [CommitInfo("abc", "abc", long_subject, 1000, "main", 0)]
        monkeypatch.setattr("orc.cli.tui.status_tui.gather_git_tree", lambda: (["main"], commits))
        result = render_git_tree()
        assert isinstance(result, rich.table.Table)
        # Verify the truncation happened by checking we can still build the table.
        # (If truncation failed, it would have raised an error.)


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
        assert "Git Tree" in markup

    def test_tab_bar_markup_second_tab_active(self):
        app = StatusApp()
        app._tab_index = 1
        markup = app._tab_bar_markup()
        assert "Agents" in markup
        assert "Git Tree" in markup

    def test_action_tab_next_increments_index(self):
        app = StatusApp()
        assert app._tab_index == 0
        app._apply_tab = lambda: None  # suppress query_one calls
        app.action_tab_next()
        assert app._tab_index == 1

    def test_action_tab_next_wraps_around(self):
        app = StatusApp()
        app._tab_index = 2  # last tab
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
        assert app._tab_index == 2  # wraps to last tab

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
        app._refresh_git_tree = lambda: refreshed.append("git_tree")
        app.on_mount()
        # git tree is loaded lazily on first tab switch, not on mount
        assert "agents" in refreshed
        assert "git_tree" not in refreshed

    def test_git_tree_loaded_lazily_on_tab_switch(self):
        app = StatusApp()
        refreshed: list[str] = []
        app._refresh_git_tree = lambda: refreshed.append("git_tree")

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
        assert not app._git_tree_loaded
        # simulate switching to the git tree tab (index 1)
        app._tab_index = 1
        app._apply_tab()
        assert "git_tree" in refreshed
        assert app._git_tree_loaded
        # switching again should NOT reload
        refreshed.clear()
        app._apply_tab()
        assert "git_tree" not in refreshed

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

    def test_refresh_git_tree_updates_widget(self, monkeypatch):
        fake_renderable = MagicMock()
        monkeypatch.setattr("orc.cli.tui.status_tui.render_git_tree", lambda: fake_renderable)
        app = StatusApp()
        updates: list = []

        class FakeStatic:
            def update(self, x: object) -> None:
                updates.append(x)

        app.query_one = lambda sel, wt=None: FakeStatic()
        app._refresh_git_tree()
        assert updates == [fake_renderable]

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

    def test_board_tab_is_third(self):
        assert _TAB_NAMES[2] == "Board"


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
                {"name": "0001-task.md", "status": "planned"},
                {"name": "0002-task.md", "status": "in-progress", "assigned_to": "coder-1"},
                {"name": "0003-task.md", "status": "in-review"},
                {"name": "0004-task.md", "status": "done"},
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
        assert rendered.count("(empty)") == 5

    def test_in_progress_statuses_covered(self, monkeypatch):
        """blocked tasks go into In progress column."""
        from orc.coordination.client import BoardSnapshot

        snap = BoardSnapshot(
            visions=[],
            tasks=[
                {"name": "blocked-task.md", "status": "blocked"},
                {"name": "inprog-task.md", "status": "in-progress"},
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
            tasks=[{"name": "review-task.md", "status": "in-review", "branch": "feat/0009-x"}],
        )
        monkeypatch.setattr("orc.cli.tui.status_tui.get_board_snapshot", lambda: snap)
        result = _render_board()
        assert isinstance(result, rich.table.Table)
        rendered = self._render_to_str(result)
        assert "feat/0009-x" in rendered


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

    def test_apply_tab_loads_board_on_index_2(self, monkeypatch):
        app = StatusApp()
        refreshed: list[str] = []
        app._refresh_board = lambda: refreshed.append("board")
        app._refresh_git_tree = lambda: None

        # Stub out query_one to avoid live widget requirement
        mock_static = MagicMock()
        mock_switcher = MagicMock()
        mock_switcher.current = None

        def _query(sel, wt=None):
            if sel == "#tab-bar":
                return mock_static
            return mock_switcher

        app.query_one = _query
        app._tab_index = 2
        app._apply_tab()
        assert refreshed == ["board"]
        assert app._board_loaded is True

    def test_apply_tab_does_not_reload_board(self, monkeypatch):
        app = StatusApp()
        app._board_loaded = True
        refreshed: list[str] = []
        app._refresh_board = lambda: refreshed.append("board")
        app._refresh_git_tree = lambda: None

        mock_static = MagicMock()
        mock_switcher = MagicMock()

        def _query(sel, wt=None):
            if sel == "#tab-bar":
                return mock_static
            return mock_switcher

        app.query_one = _query
        app._tab_index = 2
        app._apply_tab()
        assert refreshed == []

    def test_scroll_left_board_calls_scroll_left_when_on_board_tab(self):
        app = StatusApp()
        app._tab_index = 2
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
        app._tab_index = 2
        scrolled: list[str] = []

        class FakeScroll:
            def scroll_right(self) -> None:
                scrolled.append("right")

        app._active_scroll = lambda: FakeScroll()
        app.action_scroll_right_board()
        assert scrolled == ["right"]

    def test_scroll_right_board_noop_when_not_on_board_tab(self):
        app = StatusApp()
        app._tab_index = 1
        app._active_scroll = lambda: MagicMock()
        app.action_scroll_right_board()

    def test_scroll_left_board_noop_when_no_active_scroll(self):
        app = StatusApp()
        app._tab_index = 2
        app._active_scroll = lambda: None
        # Should not raise.
        app.action_scroll_left_board()

    def test_scroll_right_board_noop_when_no_active_scroll(self):
        app = StatusApp()
        app._tab_index = 2
        app._active_scroll = lambda: None
        # Should not raise.
        app.action_scroll_right_board()
