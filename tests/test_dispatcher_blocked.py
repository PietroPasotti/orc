"""Tests for orc/dispatcher.py — blocked state handling."""

from __future__ import annotations

from conftest import FakePopen, make_msg
from typer.testing import CliRunner

import orc.ai.invoke as inv
import orc.config as _cfg
import orc.engine.context as _ctx
import orc.engine.dispatcher as _disp
import orc.engine.workflow as _git_mod
import orc.main as m
from orc.ai.backends import SpawnResult

runner = CliRunner()


class TestBlockedResumption:
    def test_planner_done_is_not_blocked(
        self, monkeypatch, tmp_path, mock_git, mock_spawn, board_file
    ):
        """planner(done) is a normal terminal state — dispatcher routes to planner (no tasks)."""
        from dataclasses import replace as _replace

        board_file("counter: 1\ntasks: []\n")
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), orc_dir=tmp_path / ".orc"))

        # A vision doc gives the planner something to plan (otherwise no dispatch).
        vision_dir = tmp_path / ".orc" / "vision" / "ready"
        vision_dir.mkdir(parents=True, exist_ok=True)
        (vision_dir / "feature-x.md").write_text("# Feature X\n")

        monkeypatch.setattr("orc.git.Git.branch_exists", lambda self, b: False)
        monkeypatch.setattr("orc.git.Git.has_commits_ahead_of", lambda self, b, ref: False)
        monkeypatch.setattr("orc.git.Git.is_merged_into", lambda self, b, ref: False)
        monkeypatch.setattr("orc.git.Git.ensure_worktree", lambda self, wt, br: None)
        monkeypatch.setattr("orc.git.Git.ensure_worktree", lambda self, wt, br: None)

        import orc.messaging.telegram as tg

        done_msgs = [make_msg("[planner-1](done) 2026-03-09T10:00:00Z: All done.", ts=1000)]
        monkeypatch.setattr(tg, "_get_messages", lambda limit=100: done_msgs)
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])
        monkeypatch.setattr(_git_mod, "rebase_dev_on_main", lambda *_: None)
        monkeypatch.setattr(tg, "_send_message", lambda t: None)
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        invocations: list[str] = []
        monkeypatch.setattr(
            _ctx,
            "build_agent_context",
            lambda role, board=None, **kw: invocations.append(role) or "ctx",
        )
        monkeypatch.setattr(
            inv,
            "spawn",
            lambda *a, **kw: SpawnResult(process=FakePopen(), log_fh=None, context_tmp=""),
        )

        rc = runner.invoke(m.app, ["run", "--maxcalls", "1"])
        _ = rc
        assert invocations == ["planner"]
