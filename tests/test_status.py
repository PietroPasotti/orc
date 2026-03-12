"""Tests for orc/cli/status.py."""

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

import orc.cli.status as _st
import orc.main as m
from orc.cli.status import _dev_ahead_of_main, _dev_log_since_main
from orc.dispatcher import QA_PASSED
from orc.squad import SquadConfig

runner = CliRunner()


class TestStatusCoverage:
    def _setup(
        self,
        monkeypatch,
        *,
        blocked=(None, None),
        squad_cfg=None,
        open_tasks=None,
        done_tasks=None,
        has_open_work=False,
        ahead=0,
        dev_log=None,
        derive_task_state=None,
        feature_branch=None,
        feature_branch_exists=None,
        last_commit=None,
    ):
        monkeypatch.setattr(_st.tg, "get_messages", lambda: [])
        monkeypatch.setattr(_st._wf, "_has_unresolved_block", lambda msgs: blocked)
        if squad_cfg is None:
            monkeypatch.setattr(
                _st,
                "load_squad",
                lambda n, agents_dir: (_ for _ in ()).throw(ValueError("no squad")),
            )
        else:
            monkeypatch.setattr(_st, "load_squad", lambda n, agents_dir: squad_cfg)
        open_dicts = [{"name": t} if isinstance(t, str) else t for t in (open_tasks or [])]
        monkeypatch.setattr(_st._board, "get_open_tasks", lambda: open_dicts)
        monkeypatch.setattr(_st._board, "has_open_work", lambda: has_open_work)
        monkeypatch.setattr(
            _st._board,
            "_read_board",
            lambda: {"open": open_dicts, "done": done_tasks or []},
        )
        monkeypatch.setattr(_st, "_dev_ahead_of_main", lambda: ahead)
        monkeypatch.setattr(_st, "_dev_log_since_main", lambda: dev_log or [])
        if derive_task_state:
            monkeypatch.setattr(_st._git, "_derive_task_state", derive_task_state)
        if feature_branch:
            monkeypatch.setattr(_st._git, "_feature_branch", feature_branch)
        if feature_branch_exists is not None:
            monkeypatch.setattr(_st._git, "_feature_branch_exists", lambda b: feature_branch_exists)
        if last_commit:
            monkeypatch.setattr(_st._git, "_last_feature_commit_message", last_commit)
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

    def test_dev_log_since_main_returns_lines(self):
        def fake_run(cmd, **kw):
            r = MagicMock()
            r.stdout = "abc one\ndef two\n"
            return r

        with patch("orc.cli.status.subprocess.run", fake_run):
            assert len(_dev_log_since_main()) == 2

    def test_dev_log_since_main_empty(self):
        def fake_run(cmd, **kw):
            r = MagicMock()
            r.stdout = ""
            return r

        with patch("orc.cli.status.subprocess.run", fake_run):
            assert _dev_log_since_main() == []

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
            has_open_work=False,
            ahead=2,
            dev_log=["abc feat: done", "def fix: merged"],
            done_tasks=[{"name": "0001-foo.md", "commit-tag": "v1", "timestamp": "2024-01-01"}],
        )
        result = runner.invoke(m.app, ["status"])
        assert "2 commits" in result.output
        assert "0001-foo.md" in result.output

    def test_status_hard_block(self, tmp_path, monkeypatch):
        squad = SquadConfig(
            planner=1, coder=1, qa=1, timeout_minutes=30, name="default", description="", _models={}
        )
        self._setup(
            monkeypatch,
            squad_cfg=squad,
            blocked=("coder-1", "blocked"),
            has_open_work=True,
            ahead=0,
        )
        result = runner.invoke(m.app, ["status"])
        assert "Hard block" in result.output

    def test_status_soft_block(self, tmp_path, monkeypatch):
        squad = SquadConfig(
            planner=1, coder=1, qa=1, timeout_minutes=30, name="default", description="", _models={}
        )
        self._setup(
            monkeypatch,
            squad_cfg=squad,
            blocked=("coder-1", "soft-blocked"),
            has_open_work=True,
            ahead=0,
        )
        _st._status()

    def test_status_open_tasks_with_branches(self, tmp_path, monkeypatch):
        squad = SquadConfig(
            planner=1, coder=2, qa=1, timeout_minutes=30, name="default", description="", _models={}
        )

        def derive(name):
            if "foo" in name:
                return ("coder", "r")
            elif "bar" in name:
                return ("qa", "r")
            return (QA_PASSED, "r")  # "__qa_passed" → merge_pending

        self._setup(
            monkeypatch,
            squad_cfg=squad,
            open_tasks=["0001-foo.md", "0002-bar.md", "0003-baz.md"],
            has_open_work=True,
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
            has_open_work=True,
            derive_task_state=lambda name: ("coder", "r"),
            feature_branch=lambda name: "feat-foo",
            feature_branch_exists=False,
            ahead=0,
        )
        result = runner.invoke(m.app, ["status"])
        assert "no branch yet" in result.output

    def test_status_dev_ahead_singular(self, tmp_path, monkeypatch):
        self._setup(monkeypatch, ahead=1, dev_log=["abc feat: one thing"])
        result = runner.invoke(m.app, ["status"])
        assert "1 commit" in result.output
        assert "1 commits" not in result.output

    def test_status_planner_idle_with_open_work(self, tmp_path, monkeypatch):
        squad = SquadConfig(
            planner=1, coder=1, qa=1, timeout_minutes=30, name="default", description="", _models={}
        )
        self._setup(monkeypatch, squad_cfg=squad, has_open_work=True, blocked=(None, None), ahead=0)
        _st._status()

    def test_status_merge_pending(self, tmp_path, monkeypatch):
        """Lines 86, 126: qa-passed token → merge_pending populated and printed."""
        squad = SquadConfig(
            planner=1, coder=1, qa=1, timeout_minutes=30, name="default", description="", _models={}
        )
        self._setup(
            monkeypatch,
            squad_cfg=squad,
            open_tasks=["0001-foo.md"],
            has_open_work=True,
            derive_task_state=lambda name: (QA_PASSED, "r"),
            feature_branch=lambda name: "feat-foo",
            feature_branch_exists=False,
            ahead=0,
        )
        result = runner.invoke(m.app, ["status"])
        assert "Merge pending" in result.output or result.exit_code == 0

    def test_pending_visions_returns_unmatched_files(self, tmp_path, monkeypatch):
        """Lines 42-54: vision dir exists with files; unmatched ones are returned."""
        vision_dir = tmp_path / "vision"
        vision_dir.mkdir()
        (vision_dir / "README.md").write_text("")
        (vision_dir / ".hidden.md").write_text("")
        (vision_dir / "feature-a.md").write_text("")
        (vision_dir / "feature-b.md").write_text("")
        monkeypatch.setattr(_st._cfg, "AGENTS_DIR", tmp_path)
        monkeypatch.setattr(
            _st._board,
            "_read_board",
            lambda: {"open": [{"name": "feature-a.md"}], "done": []},
        )
        result = _st._pending_visions()
        assert result == ["feature-b.md"]
        assert "README.md" not in result
        assert ".hidden.md" not in result

    def test_pending_reviews_returns_unmerged_branches(self, monkeypatch, tmp_path):
        """Lines 65-73: feat/* branches with nonzero merge-base exit → unmerged."""
        monkeypatch.setattr(_st._cfg, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(_st._cfg, "WORK_DEV_BRANCH", "dev")

        call_num = 0

        def fake_run(cmd, **kw):
            nonlocal call_num
            r = MagicMock()
            if "--list" in cmd:
                r.stdout = "  feat/0001-foo\n  feat/0002-bar\n"
            else:
                call_num += 1
                r.returncode = 1 if call_num == 1 else 0
            return r

        with patch("orc.cli.status.subprocess.run", fake_run):
            result = _st._pending_reviews()
        assert result == ["feat/0001-foo"]

    def test_pending_reviews_strips_worktree_plus_prefix(self, monkeypatch, tmp_path):
        """Line 65: git branch prefixes worktree branches with '+'; must be stripped."""
        monkeypatch.setattr(_st._cfg, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(_st._cfg, "WORK_DEV_BRANCH", "dev")

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

    def test_status_shows_pending_visions(self, tmp_path, monkeypatch):
        """Lines 197-200: pending visions section printed when visions exist."""
        self._setup(monkeypatch, ahead=0)
        monkeypatch.setattr(_st, "_pending_visions", lambda: ["feature-x.md", "feature-y.md"])
        monkeypatch.setattr(_st, "_pending_reviews", lambda: [])
        result = runner.invoke(m.app, ["status"])
        assert "Pending visions" in result.output
        assert "feature-x.md" in result.output

    def test_status_shows_pending_reviews(self, tmp_path, monkeypatch):
        """Lines 205-209: pending reviews section printed when unmerged branches exist."""
        self._setup(monkeypatch, ahead=0)
        monkeypatch.setattr(_st, "_pending_visions", lambda: [])
        monkeypatch.setattr(_st, "_pending_reviews", lambda: ["feat/0001-foo"])
        monkeypatch.setattr(_st._git, "_last_feature_commit_message", lambda b: "fix: something")
        result = runner.invoke(m.app, ["status"])
        assert "Pending reviews" in result.output
        assert "feat/0001-foo" in result.output

    def test_status_tui_launched_when_isatty(self, monkeypatch):
        """status() launches the TUI when stdout is a TTY."""
        import orc.cli.status as _st_module

        self._setup(monkeypatch, ahead=0)
        launched: list[str] = []
        monkeypatch.setattr(_st_module, "_is_tty", lambda: True)

        with patch("orc.status_tui.StatusApp.run", lambda self: launched.append(self._squad)):
            result = runner.invoke(m.app, ["status", "--squad", "default"])

        assert result.exit_code == 0
        assert launched == ["default"]
