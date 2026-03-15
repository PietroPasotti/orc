"""Tests for orc/cli/run.py."""

from dataclasses import replace as _replace
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

import orc.cli.run as _run_mod
import orc.config as _cfg
import orc.engine.dispatcher as _disp
import orc.engine.workflow as _wf
import orc.squad as _sq
from orc.squad import SquadConfig

runner = CliRunner()


def _minimal_squad(**kw) -> SquadConfig:
    defaults = dict(
        planner=1,
        coder=1,
        qa=1,
        timeout_minutes=60,
        name="test",
        description="",
        _models={},
    )
    defaults.update(kw)
    return SquadConfig(**defaults)


def _mock_coord(monkeypatch, open_tasks=None) -> MagicMock:
    """Patch _BoardSvc and CoordinationServer so no real I/O happens in tests."""
    if open_tasks is None:
        open_tasks = [{"name": "0001-test.md"}]
    mock_state = MagicMock()
    mock_state.get_tasks.return_value = open_tasks
    mock_state.get_pending_visions.return_value = []
    mock_state.scan_todos.return_value = []
    mock_state.get_pending_reviews.return_value = []
    mock_state.get_blocked_tasks.return_value = []
    mock_state.is_empty.return_value = not open_tasks
    mock_server = MagicMock()
    monkeypatch.setattr(_run_mod, "_BoardSvc", lambda *a, **kw: mock_state)
    monkeypatch.setattr(_run_mod, "CoordinationServer", lambda *a, **kw: mock_server)
    return mock_state


class TestRunBareRaise:
    def test_run_loop_crash_reraises(
        self, tmp_path, monkeypatch, mock_validate_env, mock_telegram, mock_rebase, mock_git
    ):
        """Lines 62-67: exception from dispatcher.run() is logged and re-raised."""
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), orc_dir=tmp_path))
        monkeypatch.setattr(_sq, "load_squad", lambda *a, **kw: _minimal_squad())
        _mock_coord(monkeypatch)

        def boom(*a, **kw):
            raise RuntimeError("crashed")

        monkeypatch.setattr(_disp.Dispatcher, "run", boom)
        from unittest.mock import patch as _patch

        with _patch.object(_run_mod.logger, "exception"):
            with pytest.raises(RuntimeError, match="crashed"):
                _run_mod._run(maxcalls=1)

    def test_run_keyboard_interrupt_prints_warning(
        self, tmp_path, monkeypatch, mock_validate_env, mock_telegram, mock_rebase, mock_git, capsys
    ):
        """KeyboardInterrupt during dispatcher.run() prints warning and exits non-zero."""
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), orc_dir=tmp_path))
        monkeypatch.setattr(_sq, "load_squad", lambda *a, **kw: _minimal_squad())
        _mock_coord(monkeypatch)

        def _interrupt(*a, **kw):
            raise KeyboardInterrupt()

        monkeypatch.setattr(_disp.Dispatcher, "run", _interrupt)
        import typer

        with pytest.raises(typer.Exit) as exc_info:
            _run_mod._run(maxcalls=1)

        assert exc_info.value.exit_code == 1
        captured = capsys.readouterr()
        assert "Interrupted" in captured.err
        assert "orc run" in captured.err


def _patch_run_deps(
    monkeypatch,
    tmp_path,
    mock_validate_env,
    mock_telegram,
    mock_rebase,
    mock_git,
    *,
    dispatcher_run=None,
):
    """Monkeypatch all external dependencies for _run() tests.

    Now uses shared fixtures: mock_validate_env, mock_telegram, mock_rebase, mock_git.
    """
    monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), orc_dir=tmp_path))
    monkeypatch.setattr(_sq, "load_squad", lambda *a, **kw: _minimal_squad())
    _mock_coord(monkeypatch)
    if dispatcher_run is not None:
        monkeypatch.setattr(_disp.Dispatcher, "run", dispatcher_run)
    else:
        monkeypatch.setattr(_disp.Dispatcher, "run", lambda self, **kw: None)


class TestNoTuiFlag:
    def test_no_tui_disables_tui(
        self, tmp_path, monkeypatch, mock_validate_env, mock_telegram, mock_rebase, mock_git
    ):
        """--no-tui causes no run_tui call."""
        import orc.cli.tui as _tui_mod

        tui_called = []
        monkeypatch.setattr(_tui_mod, "run_tui", lambda state, fn: tui_called.append(True))
        _patch_run_deps(
            monkeypatch, tmp_path, mock_validate_env, mock_telegram, mock_rebase, mock_git
        )

        _run_mod._run(maxcalls=1, no_tui=True)
        assert tui_called == []

    def test_non_tty_auto_disables_tui(
        self, tmp_path, monkeypatch, mock_validate_env, mock_telegram, mock_rebase, mock_git
    ):
        """Non-TTY stdout skips TUI even without --no-tui."""
        import sys

        import orc.cli.tui as _tui_mod

        tui_called = []
        monkeypatch.setattr(_tui_mod, "run_tui", lambda state, fn: tui_called.append(True))
        monkeypatch.setattr(
            sys,
            "stdout",
            type(
                "FakeStdout",
                (),
                {
                    "isatty": lambda self: False,
                    "write": lambda self, s: None,
                    "flush": lambda self: None,
                },
            )(),
        )
        _patch_run_deps(
            monkeypatch, tmp_path, mock_validate_env, mock_telegram, mock_rebase, mock_git
        )

        _run_mod._run(maxcalls=1, no_tui=False)
        assert tui_called == []


class TestTuiPath:
    def test_tui_path_calls_run_tui_and_render(
        self, tmp_path, monkeypatch, mock_validate_env, mock_telegram, mock_rebase, mock_git
    ):
        """TTY + no --no-tui → run_tui() is called and render() is exercised."""
        import sys
        from pathlib import Path
        from unittest.mock import patch

        from conftest import FakePopen

        import orc.cli.tui as _tui_mod
        from orc.engine.pool import AgentProcess

        tui_called = []
        captured_hooks: list = []
        captured_messaging: list = []

        original_init = _disp.Dispatcher.__init__

        def capturing_init(self, squad, **kw):
            captured_hooks.append(kw.get("hooks"))
            captured_messaging.append(kw.get("messaging"))
            original_init(self, squad, **kw)

        def fake_run_tui(state, run_fn):
            tui_called.append(True)
            run_fn()  # execute dispatcher.run() synchronously in tests

        monkeypatch.setattr(_tui_mod, "run_tui", fake_run_tui)
        monkeypatch.setattr(
            sys,
            "stdout",
            type(
                "FakeTTY",
                (),
                {
                    "isatty": lambda self: True,
                    "write": lambda self, s: None,
                    "flush": lambda self: None,
                },
            )(),
        )
        monkeypatch.setattr(_run_mod, "_safe_features_done", lambda: 0)
        monkeypatch.setattr(_disp.Dispatcher, "__init__", capturing_init)
        _patch_run_deps(
            monkeypatch, tmp_path, mock_validate_env, mock_telegram, mock_rebase, mock_git
        )

        with patch.object(_tui_mod, "render", wraps=_tui_mod.render):
            _run_mod._run(maxcalls=1, no_tui=False)

        assert tui_called == [True]

        # Exercise the closures registered via hooks and messaging service.
        assert captured_hooks
        hooks = captured_hooks[0]
        assert captured_messaging
        messaging_svc = captured_messaging[0]

        # _on_agent_start
        fake_agent = AgentProcess(
            agent_id="coder-1",
            role="coder",
            model="copilot",
            task_name="t.md",
            process=FakePopen(),
            worktree=Path(tmp_path),
            log_path=tmp_path / "log",
            log_fh=None,
            context_tmp=None,
        )
        hooks.on_agent_start(fake_agent)

        # _on_agent_done
        hooks.on_agent_done(fake_agent, 0)

        # _on_orc_status
        hooks.on_orc_status("running", "checking pending work")

        # _updating_get_messages (the wrapped get_messages)
        result = messaging_svc.get_messages()
        assert isinstance(result, list)

    def test_no_tui_flag_in_cli(self, tmp_path, monkeypatch):
        """CLI --no-tui flag passes no_tui=True to _run."""
        import orc.main as _main

        called_with = {}

        def fake_run(**kw):
            called_with.update(kw)

        monkeypatch.setattr(_run_mod, "_run", fake_run)
        runner.invoke(_main.app, ["run", "--no-tui"])
        assert called_with.get("no_tui") is True


class TestPlannerDetails:
    """_on_agent_start sets details for planner agents."""

    def _make_tui_state_and_hooks(
        self,
        monkeypatch,
        tmp_path,
        mock_validate_env,
        mock_telegram,
        mock_rebase,
        mock_git,
        *,
        mock_state,
    ):
        """Helper: run _run() with TUI enabled and return (state, hooks)."""
        import sys
        from unittest.mock import MagicMock

        import orc.cli.tui as _tui_mod

        captured_hooks: list = []
        captured_state: list = []

        original_init = _disp.Dispatcher.__init__

        def capturing_init(self, squad, **kw):
            captured_hooks.append(kw.get("hooks"))
            original_init(self, squad, **kw)

        def fake_run_tui(state, run_fn):
            captured_state.append(state)
            run_fn()

        monkeypatch.setattr(_tui_mod, "run_tui", fake_run_tui)
        monkeypatch.setattr(
            sys,
            "stdout",
            type(
                "FakeTTY",
                (),
                {
                    "isatty": lambda self: True,
                    "write": lambda self, s: None,
                    "flush": lambda self: None,
                },
            )(),
        )
        monkeypatch.setattr(_run_mod, "_safe_features_done", lambda: 0)
        monkeypatch.setattr(_disp.Dispatcher, "__init__", capturing_init)
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), orc_dir=tmp_path))
        monkeypatch.setattr(_sq, "load_squad", lambda *a, **kw: _minimal_squad())
        monkeypatch.setattr(_run_mod, "_BoardSvc", lambda *a, **kw: mock_state)
        mock_server = MagicMock()
        monkeypatch.setattr(_run_mod, "CoordinationServer", lambda *a, **kw: mock_server)
        monkeypatch.setattr(_disp.Dispatcher, "run", lambda self, **kw: None)

        _run_mod._run(maxcalls=1, no_tui=False)
        return captured_state[0], captured_hooks[0]

    def test_planner_details_with_todos_and_visions(
        self, tmp_path, monkeypatch, mock_validate_env, mock_telegram, mock_rebase, mock_git
    ):
        """Planner agent gets details with todo count and vision names."""
        from pathlib import Path
        from unittest.mock import MagicMock

        from conftest import FakePopen

        from orc.engine.context import TodoItem
        from orc.engine.pool import AgentProcess

        mock_state = MagicMock()
        mock_state.get_tasks.return_value = [{"name": "0001-test.md"}]
        mock_state.get_pending_visions.return_value = ["0003-planner-status.md", "0004-foo.md"]
        mock_state.scan_todos.return_value = [
            TodoItem(file="x.py", line=1, tag="TODO", text="fix me"),
            TodoItem(file="y.py", line=2, tag="FIXME", text="and me"),
        ]
        mock_state.get_pending_reviews.return_value = []
        mock_state.get_blocked_tasks.return_value = []
        mock_state.is_empty.return_value = False

        state, hooks = self._make_tui_state_and_hooks(
            monkeypatch,
            tmp_path,
            mock_validate_env,
            mock_telegram,
            mock_rebase,
            mock_git,
            mock_state=mock_state,
        )

        fake_agent = AgentProcess(
            agent_id="planner-1",
            role="planner",
            model="copilot",
            task_name=None,
            process=FakePopen(),
            worktree=Path(tmp_path),
            log_path=tmp_path / "log",
            log_fh=None,
            context_tmp=None,
        )
        hooks.on_agent_start(fake_agent)

        assert state.agents
        agent_data = state.agents[0]
        assert agent_data.details is not None
        assert "2 todo(s)" in agent_data.details
        assert "0003-planner-status" in agent_data.details
        assert "0004-foo" in agent_data.details

    def test_planner_details_no_todos_no_visions(
        self, tmp_path, monkeypatch, mock_validate_env, mock_telegram, mock_rebase, mock_git
    ):
        """Planner details is None when no todos and no visions."""
        from pathlib import Path
        from unittest.mock import MagicMock

        from conftest import FakePopen

        from orc.engine.pool import AgentProcess

        mock_state = MagicMock()
        mock_state.get_tasks.return_value = [{"name": "0001-test.md"}]
        mock_state.get_pending_visions.return_value = []
        mock_state.scan_todos.return_value = []
        mock_state.get_pending_reviews.return_value = []
        mock_state.get_blocked_tasks.return_value = []
        mock_state.is_empty.return_value = False

        state, hooks = self._make_tui_state_and_hooks(
            monkeypatch,
            tmp_path,
            mock_validate_env,
            mock_telegram,
            mock_rebase,
            mock_git,
            mock_state=mock_state,
        )

        fake_agent = AgentProcess(
            agent_id="planner-1",
            role="planner",
            model="copilot",
            task_name=None,
            process=FakePopen(),
            worktree=Path(tmp_path),
            log_path=tmp_path / "log",
            log_fh=None,
            context_tmp=None,
        )
        hooks.on_agent_start(fake_agent)

        assert state.agents
        assert state.agents[0].details is None

    def test_coder_agent_details_is_none(
        self, tmp_path, monkeypatch, mock_validate_env, mock_telegram, mock_rebase, mock_git
    ):
        """Non-planner agents do not get details set."""
        from pathlib import Path
        from unittest.mock import MagicMock

        from conftest import FakePopen

        from orc.engine.pool import AgentProcess

        mock_state = MagicMock()
        mock_state.get_tasks.return_value = [{"name": "0001-test.md"}]
        mock_state.get_pending_visions.return_value = ["0003-planner-status.md"]
        mock_state.scan_todos.return_value = []
        mock_state.get_pending_reviews.return_value = []
        mock_state.get_blocked_tasks.return_value = []
        mock_state.is_empty.return_value = False

        state, hooks = self._make_tui_state_and_hooks(
            monkeypatch,
            tmp_path,
            mock_validate_env,
            mock_telegram,
            mock_rebase,
            mock_git,
            mock_state=mock_state,
        )

        fake_agent = AgentProcess(
            agent_id="coder-1",
            role="coder",
            model="copilot",
            task_name="0001-test.md",
            process=FakePopen(),
            worktree=Path(tmp_path),
            log_path=tmp_path / "log",
            log_fh=None,
            context_tmp=None,
        )
        hooks.on_agent_start(fake_agent)

        assert state.agents
        assert state.agents[0].details is None


class TestEarlyExit:
    def test_no_pending_work_skips_dispatcher(
        self, tmp_path, monkeypatch, mock_validate_env, mock_telegram, mock_rebase, mock_git
    ):
        """_run() exits early without creating a Dispatcher when no work."""
        _patch_run_deps(
            monkeypatch, tmp_path, mock_validate_env, mock_telegram, mock_rebase, mock_git
        )
        # Override BoardStateManager to return no open tasks so all work sources are empty.
        _mock_coord(monkeypatch, open_tasks=[])
        dispatcher_run_called = []
        monkeypatch.setattr(
            _disp.Dispatcher, "run", lambda self, **kw: dispatcher_run_called.append(True)
        )

        _run_mod._run(maxcalls=1, no_tui=True)

        assert dispatcher_run_called == [], (
            "dispatcher.run() must not be called when no pending work"
        )


class TestSafeFeaturesDone:
    def test_returns_zero_on_exception(self, monkeypatch):
        """_safe_features_done returns 0 when _count_features_done raises."""
        monkeypatch.setattr(
            _wf,
            "_count_features_done",
            lambda: (_ for _ in ()).throw(RuntimeError("git error")),
        )
        assert _run_mod._safe_features_done() == 0

    def test_returns_value_on_success(self, monkeypatch):
        """_safe_features_done returns the count on success."""
        monkeypatch.setattr(_wf, "_count_features_done", lambda: 3)
        assert _run_mod._safe_features_done() == 3


class TestMaxcallsCliValidation:
    """CLI validates --maxcalls; 0 is rejected, UNLIMITED is accepted."""

    def test_maxcalls_zero_rejected(self, tmp_path, monkeypatch):
        """--maxcalls 0 exits with an error."""
        import orc.main as m

        result = runner.invoke(m.app, ["run", "--maxcalls", "0"])
        assert result.exit_code != 0
        assert "UNLIMITED" in result.output or "must be" in result.output.lower()

    def test_maxcalls_negative_rejected(self, tmp_path, monkeypatch):
        """--maxcalls -1 exits with an error."""
        import orc.main as m

        result = runner.invoke(m.app, ["run", "--maxcalls", "-1"])
        assert result.exit_code != 0

    def test_maxcalls_invalid_string_rejected(self):
        """--maxcalls foo exits with an error."""
        import orc.main as m

        result = runner.invoke(m.app, ["run", "--maxcalls", "foo"])
        assert result.exit_code != 0

    def test_maxcalls_unlimited_accepted(self, tmp_path, monkeypatch):
        """--maxcalls UNLIMITED passes sys.maxsize to _run."""
        import sys

        called_with: dict = {}

        monkeypatch.setattr(
            _run_mod,
            "_run",
            lambda maxcalls, **kw: called_with.update(maxcalls=maxcalls),
        )
        import orc.main as m

        result = runner.invoke(m.app, ["run", "--maxcalls", "UNLIMITED"])
        assert result.exit_code == 0
        assert called_with.get("maxcalls") == sys.maxsize

    def test_maxcalls_unlimited_case_insensitive(self, tmp_path, monkeypatch):
        """--maxcalls unlimited (lowercase) is accepted."""
        import sys

        called_with: dict = {}

        monkeypatch.setattr(
            _run_mod,
            "_run",
            lambda maxcalls, **kw: called_with.update(maxcalls=maxcalls),
        )
        import orc.main as m

        result = runner.invoke(m.app, ["run", "--maxcalls", "unlimited"])
        assert result.exit_code == 0
        assert called_with.get("maxcalls") == sys.maxsize


class TestAgentCliOption:
    """CLI validates --agent and passes only_role to _run."""

    def test_agent_coder_passes_only_role(self, monkeypatch):
        called_with: dict = {}
        monkeypatch.setattr(
            _run_mod,
            "_run",
            lambda **kw: called_with.update(kw),
        )
        import orc.main as m

        result = runner.invoke(m.app, ["run", "--agent", "coder"])
        assert result.exit_code == 0
        assert called_with.get("only_role") == "coder"

    def test_agent_qa_passes_only_role(self, monkeypatch):
        called_with: dict = {}
        monkeypatch.setattr(
            _run_mod,
            "_run",
            lambda **kw: called_with.update(kw),
        )
        import orc.main as m

        result = runner.invoke(m.app, ["run", "--agent", "qa"])
        assert result.exit_code == 0
        assert called_with.get("only_role") == "qa"

    def test_agent_planner_passes_only_role(self, monkeypatch):
        called_with: dict = {}
        monkeypatch.setattr(
            _run_mod,
            "_run",
            lambda **kw: called_with.update(kw),
        )
        import orc.main as m

        result = runner.invoke(m.app, ["run", "--agent", "planner"])
        assert result.exit_code == 0
        assert called_with.get("only_role") == "planner"

    def test_agent_invalid_role_rejected(self, monkeypatch):
        monkeypatch.setattr(
            _run_mod,
            "_run",
            lambda **kw: None,
        )
        import orc.main as m

        result = runner.invoke(m.app, ["run", "--agent", "wizard"])
        assert result.exit_code != 0
        assert "Invalid agent role" in result.output

    def test_agent_none_by_default(self, monkeypatch):
        called_with: dict = {}
        monkeypatch.setattr(
            _run_mod,
            "_run",
            lambda **kw: called_with.update(kw),
        )
        import orc.main as m

        result = runner.invoke(m.app, ["run"])
        assert result.exit_code == 0
        assert called_with.get("only_role") is None

    def test_agent_case_insensitive(self, monkeypatch):
        called_with: dict = {}
        monkeypatch.setattr(
            _run_mod,
            "_run",
            lambda **kw: called_with.update(kw),
        )
        import orc.main as m

        result = runner.invoke(m.app, ["run", "--agent", "CODER"])
        assert result.exit_code == 0
        assert called_with.get("only_role") == "coder"
