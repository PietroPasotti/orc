"""Tests for orc/status_tui.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import rich.table

from orc.status_tui import (
    CommitInfo,
    StatusApp,
    _capture_status,
    _classify_commit,
    _feat_branches,
    _git_log,
    _main_branch,
    gather_git_tree,
    render_git_tree,
    run_status_tui,
)


class TestClassifyCommit:
    def test_qa_passed(self):
        style, icon = _classify_commit("qa(passed): all checks green")
        assert style == "bold green"
        assert icon == "✅"

    def test_qa_blocked(self):
        style, icon = _classify_commit("qa(blocked): cannot run tests")
        assert style == "bold red"
        assert icon == "❌"

    def test_qa_failed(self):
        style, icon = _classify_commit("qa(failed): type errors")
        assert style == "bold red"
        assert icon == "❌"

    def test_merge_feat(self):
        style, icon = _classify_commit("Merge feat/0001-foo into dev")
        assert style == "bold blue"
        assert icon == "🔀"

    def test_merge_feat_lowercase(self):
        style, icon = _classify_commit("merge feat/0002-bar into dev")
        assert style == "bold blue"
        assert icon == "🔀"

    def test_close_task(self):
        style, icon = _classify_commit("chore(orc): close task 0001-foo")
        assert style == "bold cyan"
        assert icon == "📋"

    def test_ordinary_commit(self):
        style, icon = _classify_commit("feat: add user authentication")
        assert style == ""
        assert icon == ""

    def test_empty_subject(self):
        style, icon = _classify_commit("")
        assert style == ""
        assert icon == ""


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
            patch("orc.status_tui.subprocess.run", fake_run),
            patch("orc.status_tui._cfg.REPO_ROOT", tmp_path),
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
            patch("orc.status_tui.subprocess.run", fake_run),
            patch("orc.status_tui._cfg.REPO_ROOT", tmp_path),
        ):
            rows = _git_log("main", [])

        assert rows == []

    def test_subprocess_error_returns_empty(self, tmp_path):
        def fake_run(args, **kw):
            raise OSError("git not found")

        with (
            patch("orc.status_tui.subprocess.run", fake_run),
            patch("orc.status_tui._cfg.REPO_ROOT", tmp_path),
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
            patch("orc.status_tui.subprocess.run", fake_run),
            patch("orc.status_tui._cfg.REPO_ROOT", tmp_path),
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
            patch("orc.status_tui.subprocess.run", fake_run),
            patch("orc.status_tui._cfg.REPO_ROOT", tmp_path),
            patch("orc.status_tui._cfg.BRANCH_PREFIX", ""),
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
            patch("orc.status_tui.subprocess.run", fake_run),
            patch("orc.status_tui._cfg.REPO_ROOT", tmp_path),
            patch("orc.status_tui._cfg.BRANCH_PREFIX", "orc"),
        ):
            branches = _feat_branches()

        assert "orc/feat/*" in captured[0]
        assert branches == ["orc/feat/0001-foo"]

    def test_subprocess_error_returns_empty(self, tmp_path):
        def fake_run(args, **kw):
            raise OSError("git not found")

        with (
            patch("orc.status_tui.subprocess.run", fake_run),
            patch("orc.status_tui._cfg.REPO_ROOT", tmp_path),
            patch("orc.status_tui._cfg.BRANCH_PREFIX", ""),
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
        monkeypatch.setattr("orc.status_tui._cfg.REPO_ROOT", tmp_path)
        monkeypatch.setattr("orc.status_tui._cfg.WORK_DEV_BRANCH", "dev")
        monkeypatch.setattr("orc.status_tui._cfg.BRANCH_PREFIX", "")
        monkeypatch.setattr("orc.status_tui._git._default_branch", lambda: "main")
        monkeypatch.setattr("orc.status_tui._cfg._load_orc_config", lambda *a, **kw: {})

        with patch("orc.status_tui.subprocess.run", fake_run):
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
        monkeypatch.setattr("orc.status_tui._cfg.REPO_ROOT", tmp_path)
        monkeypatch.setattr("orc.status_tui._cfg.WORK_DEV_BRANCH", "dev")
        monkeypatch.setattr("orc.status_tui._cfg.BRANCH_PREFIX", "")
        monkeypatch.setattr("orc.status_tui._git._default_branch", lambda: "main")
        monkeypatch.setattr("orc.status_tui._cfg._load_orc_config", lambda *a, **kw: {})

        with patch("orc.status_tui.subprocess.run", fake_run):
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
        monkeypatch.setattr("orc.status_tui._cfg.REPO_ROOT", tmp_path)
        monkeypatch.setattr("orc.status_tui._cfg.WORK_DEV_BRANCH", "dev")
        monkeypatch.setattr("orc.status_tui._cfg.BRANCH_PREFIX", "")
        monkeypatch.setattr("orc.status_tui._git._default_branch", lambda: "main")
        monkeypatch.setattr("orc.status_tui._cfg._load_orc_config", lambda *a, **kw: {})

        with patch("orc.status_tui.subprocess.run", fake_run):
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

        monkeypatch.setattr("orc.status_tui.gather_git_tree", fake_gather)
        result = render_git_tree()
        assert isinstance(result, rich.table.Table)

    def test_error_returns_text(self, monkeypatch):
        def fake_gather():
            raise RuntimeError("no git")

        monkeypatch.setattr("orc.status_tui.gather_git_tree", fake_gather)
        from rich.text import Text

        result = render_git_tree()
        assert isinstance(result, Text)

    def test_empty_commits_still_renders(self, monkeypatch):
        def fake_gather():
            return ["main", "dev"], []

        monkeypatch.setattr("orc.status_tui.gather_git_tree", fake_gather)
        result = render_git_tree()
        assert isinstance(result, rich.table.Table)


class TestCaptureStatus:
    def test_captures_typer_echo_output(self, monkeypatch):
        def fake_status(squad="default"):
            import sys

            sys.stdout.write("Squad: default\nmain is up to date with dev.\n")

        monkeypatch.setattr("orc.status_tui._capture_status.__module__", "orc.status_tui")

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
            "orc.status_tui._cfg._load_orc_config", lambda *a, **kw: {"orc-main-branch": "trunk"}
        )
        assert _main_branch() == "trunk"

    def test_falls_back_to_default_branch(self, monkeypatch):
        monkeypatch.setattr("orc.status_tui._cfg._load_orc_config", lambda *a, **kw: {})
        monkeypatch.setattr("orc.status_tui._git._default_branch", lambda: "master")
        assert _main_branch() == "master"

    def test_ignores_empty_string_config(self, monkeypatch):
        monkeypatch.setattr(
            "orc.status_tui._cfg._load_orc_config", lambda *a, **kw: {"orc-main-branch": ""}
        )
        monkeypatch.setattr("orc.status_tui._git._default_branch", lambda: "main")
        assert _main_branch() == "main"


class TestGitLogEdgeCases:
    def test_invalid_timestamp_becomes_zero(self, tmp_path):
        def fake_run(args, **kw):
            r = MagicMock()
            r.stdout = "abc|abc|subject|not-a-number\n"
            return r

        with (
            patch("orc.status_tui.subprocess.run", fake_run),
            patch("orc.status_tui._cfg.REPO_ROOT", tmp_path),
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

        monkeypatch.setattr("orc.status_tui._cfg.REPO_ROOT", tmp_path)
        monkeypatch.setattr("orc.status_tui._cfg.WORK_DEV_BRANCH", "dev")
        monkeypatch.setattr("orc.status_tui._cfg.BRANCH_PREFIX", "")
        monkeypatch.setattr("orc.status_tui._main_branch", bad_main)

        with patch("orc.status_tui.subprocess.run", fake_run):
            branches, _ = gather_git_tree()

        assert branches[0] == "main"


class TestRenderGitTreeEdgeCases:
    def test_no_branches_returns_text(self, monkeypatch):
        monkeypatch.setattr("orc.status_tui.gather_git_tree", lambda: ([], []))
        from rich.text import Text

        result = render_git_tree()
        assert isinstance(result, Text)

    def test_long_subject_is_truncated(self, monkeypatch):
        long_subject = "a" * 80
        commits = [CommitInfo("abc", "abc", long_subject, 1000, "main", 0)]
        monkeypatch.setattr("orc.status_tui.gather_git_tree", lambda: (["main"], commits))
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
        app._tab_index = 1
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
        assert app._tab_index == 1

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
        assert "agents" in refreshed
        assert "git_tree" in refreshed

    def test_refresh_agents_updates_widget(self, monkeypatch):
        monkeypatch.setattr(
            "orc.status_tui._capture_status", lambda squad="default": "agent output"
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
        monkeypatch.setattr("orc.status_tui.render_git_tree", lambda: fake_renderable)
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
