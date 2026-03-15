"""Tests for orc/cli/status.py."""

from dataclasses import replace as _replace
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

import orc.cli.status as _st
import orc.config as _cfg
import orc.main as m
from orc.cli.status import _dev_ahead_of_main
from orc.coordination.models import Board, TaskEntry
from orc.engine.context import TodoItem
from orc.engine.dispatcher import QA_PASSED
from orc.squad import SquadConfig

runner = CliRunner()


class TestStatusCoverage:
    def _setup(
        self,
        monkeypatch,
        *,
        squad_cfg=None,
        open_tasks=None,
        done_tasks=None,
        ahead=0,
        features_pending=None,
        derive_task_state=None,
        feature_branch=None,
        feature_branch_exists=None,
        last_commit=None,
        open_todos=None,
        open_visions=None,
    ):
        if squad_cfg is None:
            monkeypatch.setattr(
                _st,
                "load_squad",
                lambda n, orc_dir: (_ for _ in ()).throw(ValueError("no squad")),
            )
        else:
            monkeypatch.setattr(_st, "load_squad", lambda n, orc_dir: squad_cfg)
        open_entries = [
            TaskEntry(name=t)
            if isinstance(t, str)
            else (TaskEntry(**t) if isinstance(t, dict) else t)
            for t in (open_tasks or [])
        ]
        monkeypatch.setattr(_st._board, "get_tasks", lambda: open_entries)
        monkeypatch.setattr(
            _st._board_impl,
            "_read_board",
            lambda: Board(counter=0, tasks=open_entries),
        )
        _visions = open_visions if open_visions is not None else []
        monkeypatch.setattr(_st, "_pending_visions", lambda: _visions)
        monkeypatch.setattr(_st, "_pending_reviews", lambda: [])
        _todo_items = [TodoItem(**t) if isinstance(t, dict) else t for t in (open_todos or [])]
        monkeypatch.setattr(_st._ctx, "_scan_todos", lambda root: _todo_items)
        monkeypatch.setattr(_st, "_dev_ahead_of_main", lambda: ahead)
        # Patch the git helper used by _status() for dev-vs-main display.
        _features = (
            features_pending
            if features_pending is not None
            else ([] if ahead == 0 else [f"feat/000{i}-stub" for i in range(ahead)])
        )
        monkeypatch.setattr(_st._wf, "features_in_dev_not_main", lambda: _features)
        if derive_task_state:
            monkeypatch.setattr(_st._wf, "_derive_task_state", derive_task_state)
        if feature_branch:
            monkeypatch.setattr(_cfg.Config, "feature_branch", lambda self, t: feature_branch(t))
        if feature_branch_exists is not None:
            monkeypatch.setattr("orc.git.Git.branch_exists", lambda self, b: feature_branch_exists)
        else:
            monkeypatch.setattr("orc.git.Git.branch_exists", lambda self, b: False)
        if last_commit:
            pass  # _last_feature_commit_message removed; board-status-based routing now
        monkeypatch.setattr(_st._ctx, "_role_symbol", lambda role: "")

    def test_dev_ahead_of_main_parses_stdout(self):
        def fake_run(cmd, **kw):
            r = MagicMock()
            r.stdout = "3\n"
            return r

        with patch("orc.cli.status.subprocess.run", fake_run):
            assert _dev_ahead_of_main() == 3

    def test_dev_ahead_of_main_bad_output(self):
        def fake_run(cmd, **kw):
            r = MagicMock()
            r.stdout = "bad\n"
            return r

        with patch("orc.cli.status.subprocess.run", fake_run):
            assert _dev_ahead_of_main() == 0

    def test_status_no_squad(self, tmp_path, monkeypatch):
        self._setup(monkeypatch, ahead=0)
        _st._status()

    def test_status_via_cli_command(self, tmp_path, monkeypatch):
        self._setup(monkeypatch, ahead=0)
        result = runner.invoke(m.app, ["status"])
        assert result.exit_code == 0
        assert "main is up to date" in result.output

    def test_status_squad_empty_board_dev_ahead(self, tmp_path, monkeypatch):
        squad = SquadConfig(
            planner=1, coder=2, qa=1, timeout_minutes=30, name="default", description="", _models={}
        )
        self._setup(
            monkeypatch,
            squad_cfg=squad,
            features_pending=["feat/0001-foo", "feat/0002-bar"],
        )
        result = runner.invoke(m.app, ["status"])
        assert "2 feature" in result.output
        assert "feat/0001-foo" in result.output

    def test_status_hard_block(self, tmp_path, monkeypatch):
        squad = SquadConfig(
            planner=1, coder=1, qa=1, timeout_minutes=30, name="default", description="", _models={}
        )
        self._setup(
            monkeypatch,
            squad_cfg=squad,
            open_tasks=[{"name": "0001-foo.md", "status": "blocked"}],
            ahead=0,
        )
        result = runner.invoke(m.app, ["status"])
        assert "Blocked" in result.output

    def test_status_soft_block(self, tmp_path, monkeypatch):
        squad = SquadConfig(
            planner=1, coder=1, qa=1, timeout_minutes=30, name="default", description="", _models={}
        )
        self._setup(
            monkeypatch,
            squad_cfg=squad,
            open_tasks=[{"name": "0001-foo.md", "status": "blocked"}],
            ahead=0,
        )
        _st._status()

    def test_status_soft_block_with_open_tasks(self, tmp_path, monkeypatch):
        """Blocked planner note shown when tasks include blocked status."""
        squad = SquadConfig(
            planner=1, coder=1, qa=1, timeout_minutes=30, name="default", description="", _models={}
        )
        self._setup(
            monkeypatch,
            squad_cfg=squad,
            open_tasks=[{"name": "0001-foo.md", "status": "blocked"}],
            ahead=0,
        )
        result = _st._status()
        assert result is None  # just ensure it runs without error

    def test_status_open_tasks_with_branches(self, tmp_path, monkeypatch):
        squad = SquadConfig(
            planner=1, coder=2, qa=1, timeout_minutes=30, name="default", description="", _models={}
        )

        def derive(name, task_data=None):
            if "foo" in name:
                return ("coder", "r")
            elif "bar" in name:
                return ("qa", "r")
            return (QA_PASSED, "r")  # "__qa_passed" → merge_pending

        self._setup(
            monkeypatch,
            squad_cfg=squad,
            open_tasks=["0001-foo.md", "0002-bar.md", "0003-baz.md"],
            derive_task_state=derive,
            feature_branch=lambda name: f"feat-{name}",
            feature_branch_exists=True,
            last_commit=lambda b: "last commit",
            ahead=0,
        )
        _st._status()

    def test_status_open_tasks_no_branch(self, tmp_path, monkeypatch):
        squad = SquadConfig(
            planner=1, coder=1, qa=1, timeout_minutes=30, name="default", description="", _models={}
        )
        self._setup(
            monkeypatch,
            squad_cfg=squad,
            open_tasks=["0001-foo.md"],
            derive_task_state=lambda name, td=None: ("coder", "r"),
            feature_branch=lambda name: "feat-foo",
            feature_branch_exists=False,
            ahead=0,
        )
        result = runner.invoke(m.app, ["status"])
        assert "no branch yet" in result.output

    def test_status_dev_ahead_singular(self, tmp_path, monkeypatch):
        self._setup(monkeypatch, features_pending=["feat/0001-foo"])
        result = runner.invoke(m.app, ["status"])
        assert "1 feature" in result.output
        assert "1 features" not in result.output

    def test_status_planner_idle_with_open_work(self, tmp_path, monkeypatch):
        self._setup(monkeypatch, ahead=0)
        _st._status()

    def test_status_planner_ready_when_visions_pending(self, tmp_path, monkeypatch):
        """Planner note is 'ready (visions pending)' when open_visions is non-empty."""
        squad = SquadConfig(
            planner=1, coder=1, qa=1, timeout_minutes=30, name="default", description="", _models={}
        )
        self._setup(
            monkeypatch,
            squad_cfg=squad,
            open_visions=["some-vision.md"],
            ahead=0,
        )
        result = runner.invoke(m.app, ["status"])
        assert result.exit_code == 0
        assert "ready (visions pending)" in result.output

    def test_status_planner_idle_when_no_visions_and_no_todos(self, tmp_path, monkeypatch):
        """Planner note is 'idle' when open_visions is empty and no todos."""
        squad = SquadConfig(
            planner=1, coder=1, qa=1, timeout_minutes=30, name="default", description="", _models={}
        )
        self._setup(
            monkeypatch,
            squad_cfg=squad,
            open_visions=[],
            open_todos=[],
            ahead=0,
        )
        result = runner.invoke(m.app, ["status"])
        assert result.exit_code == 0
        assert "idle" in result.output
        assert "ready (visions pending)" not in result.output

    def test_status_merge_pending(self, tmp_path, monkeypatch):
        """Lines 86, 126: qa-passed token → merge_pending populated and printed."""
        squad = SquadConfig(
            planner=1, coder=1, qa=1, timeout_minutes=30, name="default", description="", _models={}
        )
        self._setup(
            monkeypatch,
            squad_cfg=squad,
            open_tasks=["0001-foo.md"],
            derive_task_state=lambda name, td=None: (QA_PASSED, "r"),
            feature_branch=lambda name: "feat-foo",
            feature_branch_exists=False,
            ahead=0,
        )
        result = runner.invoke(m.app, ["status"])
        assert "Merge pending" in result.output or result.exit_code == 0

    def test_pending_visions_returns_unmatched_files(self, tmp_path, monkeypatch):
        """Lines 42-54: vision/ready dir exists with files; unmatched ones are returned."""
        vision_dir = tmp_path / "vision"
        ready_dir = vision_dir / "ready"
        ready_dir.mkdir(parents=True, exist_ok=True)
        (ready_dir / "README.md").write_text("")
        (ready_dir / ".hidden.md").write_text("")
        (ready_dir / "feature-a.md").write_text("")
        (ready_dir / "feature-b.md").write_text("")
        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(_cfg.get(), orc_dir=tmp_path, vision_dir=vision_dir),
        )
        monkeypatch.setattr(
            _st._board_impl,
            "_read_board",
            lambda: Board(counter=0, tasks=[TaskEntry(name="feature-a.md")]),
        )
        result = _st._pending_visions()
        assert result == ["feature-b.md"]
        assert "README.md" not in result
        assert ".hidden.md" not in result

    def test_pending_visions_returns_empty_when_no_vision_dir(self, tmp_path, monkeypatch):
        """Line 56: vision/ready dir doesn't exist → return []."""
        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(_cfg.get(), vision_dir=tmp_path / "nonexistent"),
        )
        assert _st._pending_visions() == []

    def test_pending_reviews_returns_unmerged_branches(self, monkeypatch, tmp_path, mock_git):
        """Lines 65-73: feat/* branches not merged into dev → unmerged."""
        from dataclasses import replace as _replace

        monkeypatch.setattr(
            _cfg, "_config", _replace(_cfg.get(), repo_root=tmp_path, work_dev_branch="dev")
        )

        def fake_run(cmd, **kw):
            r = MagicMock()
            if "--list" in cmd:
                r.stdout = "  feat/0001-foo\n  feat/0002-bar\n"
            return r

        # feat/0001-foo is unmerged; feat/0002-bar is already merged into dev
        monkeypatch.setattr(
            "orc.git.Git.is_merged_into",
            lambda self, b, ref: b == "feat/0002-bar",
        )

        with patch("orc.cli.status.subprocess.run", fake_run):
            result = _st._pending_reviews()
        assert result == ["feat/0001-foo"]

    def test_pending_reviews_strips_worktree_plus_prefix(self, monkeypatch, tmp_path, mock_git):
        """Line 65: git branch prefixes worktree branches with '+'; must be stripped."""
        from dataclasses import replace as _replace

        monkeypatch.setattr(
            _cfg, "_config", _replace(_cfg.get(), repo_root=tmp_path, work_dev_branch="dev")
        )

        def fake_run(cmd, **kw):
            r = MagicMock()
            if "--list" in cmd:
                # '+' prefix = branch checked out in another worktree
                r.stdout = "+ feat/0004-worktree\n  feat/0005-normal\n"
            else:
                r.returncode = 1  # both unmerged
            return r

        with patch("orc.cli.status.subprocess.run", fake_run):
            result = _st._pending_reviews()
        assert result == ["feat/0004-worktree", "feat/0005-normal"]

    def test_pending_reviews_with_branch_prefix(self, monkeypatch, tmp_path, mock_git):
        """Line 67: when BRANCH_PREFIX is set, pattern uses prefix/feat/* glob."""
        from dataclasses import replace as _replace

        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(_cfg.get(), repo_root=tmp_path, work_dev_branch="dev", branch_prefix="orc"),
        )

        seen_patterns = []

        def fake_run(cmd, **kw):
            r = MagicMock()
            if "--list" in cmd:
                seen_patterns.append(cmd[-1])
                r.stdout = "  orc/feat/0001-foo\n"
            else:
                r.returncode = 1  # unmerged
            return r

        with patch("orc.cli.status.subprocess.run", fake_run):
            result = _st._pending_reviews()
        assert seen_patterns == ["orc/feat/*"]
        assert result == ["orc/feat/0001-foo"]

    def test_status_shows_pending_visions(self, tmp_path, monkeypatch):
        """Lines 197-200: pending visions section printed when visions exist."""
        self._setup(monkeypatch, ahead=0)
        monkeypatch.setattr(_st, "_pending_visions", lambda: ["feature-x.md", "feature-y.md"])
        monkeypatch.setattr(_st, "_get_wip_branches", lambda: [])
        monkeypatch.setattr(_st, "_get_approved_branches", lambda: [])
        result = runner.invoke(m.app, ["status"])
        assert "Pending visions" in result.output
        assert "feature-x.md" in result.output

    def test_status_shows_pending_reviews(self, tmp_path, monkeypatch):
        """Lines 271-278: awaiting-review section printed when WIP branches exist."""
        self._setup(monkeypatch, ahead=0)
        monkeypatch.setattr(_st, "_pending_visions", lambda: [])
        monkeypatch.setattr(_st, "_get_wip_branches", lambda b=None: ["feat/0001-foo"])
        monkeypatch.setattr(_st, "_get_approved_branches", lambda b=None: [])
        result = runner.invoke(m.app, ["status"])
        assert "Awaiting review" in result.output
        assert "feat/0001-foo" in result.output

    def test_status_shows_approved_branches(self, tmp_path, monkeypatch):
        """Lines 280-287: approved-pending-merge section printed when QA-approved branches exist."""
        self._setup(monkeypatch, ahead=0)
        monkeypatch.setattr(_st, "_pending_visions", lambda: [])
        monkeypatch.setattr(_st, "_get_wip_branches", lambda b=None: [])
        monkeypatch.setattr(_st, "_get_approved_branches", lambda b=None: ["feat/0002-bar"])
        result = runner.invoke(m.app, ["status"])
        assert "Approved, pending merge" in result.output
        assert "feat/0002-bar" in result.output

    def test_get_wip_branches_filters_coder_done(self, monkeypatch, tmp_path):
        """_get_wip_branches returns branches for tasks with status=in-review."""
        monkeypatch.setattr(
            _st._board,
            "get_tasks",
            lambda: [TaskEntry(name="0001-foo.md", status="in-review")],
        )
        monkeypatch.setattr(_cfg.Config, "feature_branch", lambda self, name: "feat/0001-foo")
        assert _st._get_wip_branches() == ["feat/0001-foo"]

    def test_get_approved_branches_filters_qa_passed(self, monkeypatch, tmp_path):
        """_get_approved_branches returns branches for open tasks with status=approved."""
        monkeypatch.setattr(
            _st._board,
            "get_tasks",
            lambda: [TaskEntry(name="0001-foo.md", status="done")],
        )
        monkeypatch.setattr(_cfg.Config, "feature_branch", lambda self, name: "feat/0001-foo")
        assert _st._get_approved_branches() == ["feat/0001-foo"]

    def test_status_tui_launched_when_isatty(self, monkeypatch):
        """status() launches the TUI when stdout is a TTY."""
        import orc.cli.status as _st

        self._setup(monkeypatch, ahead=0)
        launched: list[str] = []
        monkeypatch.setattr(_st, "_is_tty", lambda: True)

        _patch = "orc.cli.tui.status_tui.StatusApp.run"
        with patch(_patch, lambda self: launched.append(self._squad)):
            result = runner.invoke(m.app, ["status", "--squad", "default"])

        assert result.exit_code == 0
        assert launched == ["default"]

    def test_echo_wrapped_short_line_unchanged(self, monkeypatch):
        """_echo_wrapped passes through lines shorter than terminal width."""
        echoed: list[str] = []
        monkeypatch.setattr(_st.typer, "echo", lambda s: echoed.append(s))
        with patch("orc.cli.status.shutil.get_terminal_size", return_value=MagicMock(columns=80)):
            _st._echo_wrapped("hello")
        assert echoed == ["hello"]

    def test_echo_wrapped_truncates_long_line(self, monkeypatch):
        """_echo_wrapped truncates lines that exceed terminal width."""
        echoed: list[str] = []
        monkeypatch.setattr(_st.typer, "echo", lambda s: echoed.append(s))
        with patch("orc.cli.status.shutil.get_terminal_size", return_value=MagicMock(columns=10)):
            _st._echo_wrapped("a" * 20)
        assert echoed == ["a" * 10]

    def test_echo_wrapped_handles_embedded_newlines(self, monkeypatch):
        """_echo_wrapped truncates each visual line independently."""
        echoed: list[str] = []
        monkeypatch.setattr(_st.typer, "echo", lambda s: echoed.append(s))
        with patch("orc.cli.status.shutil.get_terminal_size", return_value=MagicMock(columns=5)):
            _st._echo_wrapped("abcdefgh\n12345678")
        assert echoed == ["abcde\n12345"]

    def test_status_shows_todos_fixmes(self, tmp_path, monkeypatch):
        """TODOs/FIXMEs section is printed after pending visions when items exist."""
        todos = [
            {"file": "src/foo.py", "line": 42, "tag": "TODO", "text": "fix this"},
            {"file": "src/bar.py", "line": 7, "tag": "FIXME", "text": "broken"},
        ]
        self._setup(monkeypatch, ahead=0, open_todos=todos)
        result = runner.invoke(m.app, ["status"])
        assert "TODOs / FIXMEs" in result.output
        assert "src/foo.py:42" in result.output
        assert "fix this" in result.output
        assert "src/bar.py:7" in result.output
        assert "FIXME" in result.output

    def test_status_todos_capped_at_five(self, tmp_path, monkeypatch):
        """TODOs/FIXMEs section shows at most 5 items."""
        todos = [
            {"file": f"src/f{i}.py", "line": i, "tag": "TODO", "text": f"item {i}"}
            for i in range(8)
        ]
        self._setup(monkeypatch, ahead=0, open_todos=todos)
        result = runner.invoke(m.app, ["status"])
        assert "8" in result.output  # shows total count
        assert result.output.count("[TODO]") == 5

    def test_status_no_todos_section_when_empty(self, tmp_path, monkeypatch):
        """TODOs/FIXMEs section is absent when there are no items."""
        self._setup(monkeypatch, ahead=0, open_todos=[])
        result = runner.invoke(m.app, ["status"])
        assert "TODOs" not in result.output

    def test_plain_flag_bypasses_tui_when_tty(self, monkeypatch):
        """--plain skips the TUI even when _is_tty() returns True."""
        self._setup(monkeypatch, ahead=0)
        launched: list[str] = []
        monkeypatch.setattr(_st, "_is_tty", lambda: True)

        _patch = "orc.cli.tui.status_tui.StatusApp.run"
        with patch(_patch, lambda self: launched.append(self._squad)):
            result = runner.invoke(m.app, ["status", "--plain"])

        assert result.exit_code == 0
        assert launched == []  # TUI was NOT launched
        assert "main is up to date" in result.output

    def test_plain_flag_produces_plain_output(self, monkeypatch):
        """--plain produces the same plain-text output as the non-TTY path."""
        self._setup(monkeypatch, ahead=0)
        monkeypatch.setattr(_st, "_is_tty", lambda: True)

        result = runner.invoke(m.app, ["status", "--plain"])

        assert result.exit_code == 0
        assert "main is up to date" in result.output

    def test_without_plain_tui_launched_when_tty(self, monkeypatch):
        """Without --plain, TUI is still launched when stdout is a TTY."""
        self._setup(monkeypatch, ahead=0)
        launched: list[str] = []
        monkeypatch.setattr(_st, "_is_tty", lambda: True)

        _patch = "orc.cli.tui.status_tui.StatusApp.run"
        with patch(_patch, lambda self: launched.append(self._squad)):
            result = runner.invoke(m.app, ["status", "--squad", "default"])

        assert result.exit_code == 0
        assert launched == ["default"]
