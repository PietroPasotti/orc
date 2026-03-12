"""Tests for orc/cli/run.py."""

import pytest
from typer.testing import CliRunner

import orc.board as _board
import orc.cli.merge as _merge_mod
import orc.cli.run as _run_mod
import orc.config as _cfg
import orc.dispatcher as _disp
import orc.git.core as _git
import orc.messaging.telegram as tg
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


class TestRunBareRaise:
    def test_run_loop_crash_reraises(self, tmp_path, monkeypatch):
        """Lines 62-67: exception from dispatcher.run() is logged and re-raised."""
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path)
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])
        monkeypatch.setattr(_sq, "load_squad", lambda *a, **kw: _minimal_squad())
        monkeypatch.setattr(tg, "get_messages", lambda: [])
        monkeypatch.setattr(_merge_mod, "_rebase_dev_on_main", lambda msgs, squad: None)
        monkeypatch.setattr(_board, "clear_all_assignments", lambda: None)
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)
        monkeypatch.setattr(
            _disp.Dispatcher, "has_pending_work", staticmethod(lambda cb, msgs: True)
        )

        def boom(*a, **kw):
            raise RuntimeError("crashed")

        monkeypatch.setattr(_disp.Dispatcher, "run", boom)
        from unittest.mock import patch as _patch

        with _patch.object(_run_mod.logger, "exception"):
            with pytest.raises(RuntimeError, match="crashed"):
                _run_mod._run(maxcalls=1)


def _patch_run_deps(monkeypatch, tmp_path, *, dispatcher_run=None):
    """Monkeypatch all external dependencies for _run() tests."""
    monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path)
    monkeypatch.setattr(_cfg, "validate_env", lambda: [])
    monkeypatch.setattr(_sq, "load_squad", lambda *a, **kw: _minimal_squad())
    monkeypatch.setattr(tg, "get_messages", lambda: [])
    monkeypatch.setattr(_merge_mod, "_rebase_dev_on_main", lambda msgs, squad: None)
    monkeypatch.setattr(_board, "clear_all_assignments", lambda: None)
    monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)
    monkeypatch.setattr(_disp.Dispatcher, "has_pending_work", staticmethod(lambda cb, msgs: True))
    if dispatcher_run is not None:
        monkeypatch.setattr(_disp.Dispatcher, "run", dispatcher_run)
    else:
        monkeypatch.setattr(_disp.Dispatcher, "run", lambda self, **kw: None)


class TestNoTuiFlag:
    def test_no_tui_disables_tui(self, tmp_path, monkeypatch):
        """--no-tui causes no run_tui call."""
        import orc.tui as _tui_mod

        tui_called = []
        monkeypatch.setattr(_tui_mod, "run_tui", lambda state, fn: tui_called.append(True))
        _patch_run_deps(monkeypatch, tmp_path)

        _run_mod._run(maxcalls=1, no_tui=True)
        assert tui_called == []

    def test_non_tty_auto_disables_tui(self, tmp_path, monkeypatch):
        """Non-TTY stdout skips TUI even without --no-tui."""
        import sys

        import orc.tui as _tui_mod

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
        _patch_run_deps(monkeypatch, tmp_path)

        _run_mod._run(maxcalls=1, no_tui=False)
        assert tui_called == []


class TestTuiPath:
    def test_tui_path_calls_run_tui_and_render(self, tmp_path, monkeypatch):
        """TTY + no --no-tui → run_tui() is called and render() is exercised."""
        import sys
        from pathlib import Path
        from unittest.mock import patch

        from conftest import FakePopen

        import orc.tui as _tui_mod
        from orc.pool import AgentProcess

        tui_called = []
        captured_callbacks: list = []

        original_init = _disp.Dispatcher.__init__

        def capturing_init(self, squad, callbacks, **kw):
            captured_callbacks.append(callbacks)
            original_init(self, squad, callbacks, **kw)

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
        monkeypatch.setattr(_run_mod, "_safe_dev_ahead", lambda: 0)
        monkeypatch.setattr(_disp.Dispatcher, "__init__", capturing_init)
        _patch_run_deps(monkeypatch, tmp_path)

        with patch.object(_tui_mod, "render", wraps=_tui_mod.render):
            _run_mod._run(maxcalls=1, no_tui=False)

        assert tui_called == [True]

        # Exercise the closures registered in callbacks.
        assert captured_callbacks
        cb = captured_callbacks[0]

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
        )
        cb.on_agent_start(fake_agent)

        # _on_agent_done
        cb.on_agent_done(fake_agent, 0)

        # _updating_get_messages (the wrapped get_messages)
        result = cb.get_messages()
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


class TestEarlyExit:
    def test_no_pending_work_skips_dispatcher(self, tmp_path, monkeypatch):
        """_run() exits early without creating a Dispatcher when has_pending_work is False."""
        _patch_run_deps(monkeypatch, tmp_path)
        monkeypatch.setattr(
            _disp.Dispatcher, "has_pending_work", staticmethod(lambda cb, msgs: False)
        )
        dispatcher_run_called = []
        monkeypatch.setattr(
            _disp.Dispatcher, "run", lambda self, **kw: dispatcher_run_called.append(True)
        )

        _run_mod._run(maxcalls=1, no_tui=True)

        assert dispatcher_run_called == [], (
            "dispatcher.run() must not be called when no pending work"
        )


class TestSafeDevAhead:
    def test_returns_zero_on_exception(self, monkeypatch):
        """_safe_dev_ahead returns 0 when _dev_ahead_of_main raises."""
        import orc.cli.status as _status_mod

        monkeypatch.setattr(
            _status_mod,
            "_dev_ahead_of_main",
            lambda: (_ for _ in ()).throw(RuntimeError("git error")),
        )
        assert _run_mod._safe_dev_ahead() == 0

    def test_returns_value_on_success(self, monkeypatch):
        """_safe_dev_ahead returns the git count on success."""
        import orc.cli.status as _status_mod

        monkeypatch.setattr(_status_mod, "_dev_ahead_of_main", lambda: 3)
        assert _run_mod._safe_dev_ahead() == 3
