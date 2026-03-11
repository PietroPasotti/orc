"""Tests for orc/cli/run.py."""

import pytest
from typer.testing import CliRunner

import orc.board as _board
import orc.cli.merge as _merge_mod
import orc.cli.run as _run_mod
import orc.config as _cfg
import orc.dispatcher as _disp
import orc.git as _git
import orc.squad as _sq
import orc.telegram as tg
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

        def boom(*a, **kw):
            raise RuntimeError("crashed")

        monkeypatch.setattr(_disp.Dispatcher, "run", boom)
        from unittest.mock import patch as _patch

        with _patch.object(_run_mod.logger, "exception"):
            with pytest.raises(RuntimeError, match="crashed"):
                _run_mod._run(maxloops=1)
