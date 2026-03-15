"""Tests for orc/dispatcher.py — blocked state handling."""

from __future__ import annotations

from conftest import FakePopen, make_msg
from typer.testing import CliRunner

import orc.ai.invoke as inv
import orc.config as _cfg
import orc.engine.context as _ctx
import orc.engine.dispatcher as _disp
import orc.git.core as _git_mod
import orc.main as m
from orc.ai.backends import SpawnResult

runner = CliRunner()


class TestBlockedResumption:
    def test_planner_done_is_not_blocked(
        self, monkeypatch, tmp_path, mock_git, mock_spawn, board_file
    ):
        """planner(done) is a normal terminal state — dispatcher routes to planner (no tasks)."""
        from dataclasses import replace as _replace

        import orc.git.core as _git

        board_file("counter: 1\ntasks: []\n")
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), orc_dir=tmp_path / ".orc"))

        # A vision doc gives the planner something to plan (otherwise no dispatch).
        vision_dir = tmp_path / ".orc" / "vision" / "ready"
        vision_dir.mkdir(parents=True, exist_ok=True)
        (vision_dir / "feature-x.md").write_text("# Feature X\n")

        monkeypatch.setattr(_git, "_feature_branch_exists", lambda b: False)
        monkeypatch.setattr(_git, "_feature_has_commits_ahead_of_main", lambda b: False)
        monkeypatch.setattr(_git, "_feature_merged_into_dev", lambda b: False)
        monkeypatch.setattr(_git, "_ensure_feature_worktree", lambda task: tmp_path)
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)

        import orc.messaging.telegram as tg

        done_msgs = [make_msg("[planner-1](done) 2026-03-09T10:00:00Z: All done.", ts=1000)]
        monkeypatch.setattr(tg, "get_messages", lambda: done_msgs)
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])
        monkeypatch.setattr(_git_mod, "_rebase_dev_on_main", lambda *_: None)
        monkeypatch.setattr(tg, "send_message", lambda t: None)
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        invocations: list[str] = []
        monkeypatch.setattr(
            _ctx,
            "build_agent_context",
            lambda name, msgs, board, **kw: invocations.append(name) or ("model", "ctx"),
        )
        monkeypatch.setattr(
            inv,
            "spawn",
            lambda *a, **kw: SpawnResult(process=FakePopen(), log_fh=None, context_tmp=""),
        )

        rc = runner.invoke(m.app, ["run", "--maxcalls", "1"])
        _ = rc
        assert invocations == ["planner"]
