"""Tests for orc/dispatcher.py — blocked state recovery."""

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
    def _blocked_msgs(self, agent_id: str) -> list[dict]:
        """agent_id should be in 'role-N' format, e.g. 'coder-1'."""
        return [make_msg(f"[{agent_id}](blocked) 2026-03-09T11:00:00Z: Need help.", ts=1000)]

    def test_blocked_agent_resumes_after_reply(
        self,
        monkeypatch,
        tmp_path,
        mock_git,
        mock_telegram,
        mock_spawn,
        board_file,
        mock_validate_env,
        mock_rebase,
    ):
        board_file("counter: 1\nopen:\n  - name: 0001-foo.md\n")

        blocked = self._blocked_msgs("coder-1")
        msg_iter = iter([blocked, []])

        from unittest.mock import MagicMock

        import orc.cli.run as _run_mod
        import orc.messaging.telegram as tg

        monkeypatch.setattr(tg, "get_messages", lambda: next(msg_iter, []))

        invocations: list[str] = []
        monkeypatch.setattr(
            _ctx,
            "build_agent_context",
            lambda name, msgs, **kw: invocations.append(name) or ("model", "ctx"),
        )
        monkeypatch.setattr(_ctx, "wait_for_human_reply", lambda msgs, **kw: "Here's the fix.")
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        monkeypatch.setattr(_run_mod, "logger", MagicMock())

        rc = runner.invoke(m.app, ["run", "--maxcalls", "1"])
        assert rc.exit_code == 0
        assert invocations == ["coder"]

    def test_blocked_resumes_correct_agent(
        self, monkeypatch, tmp_path, mock_git, mock_spawn, board_file
    ):
        """After a hard-block reply, the dispatcher routes to the correct role."""
        from dataclasses import replace as _replace

        import orc.git.core as _git

        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), orc_dir=tmp_path / ".orc"))
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])
        monkeypatch.setattr(_git_mod, "_rebase_dev_on_main", lambda *_: None)

        import orc.messaging.telegram as tg

        monkeypatch.setattr(tg, "send_message", lambda t: None)
        monkeypatch.setattr(_ctx, "wait_for_human_reply", lambda msgs, **kw: "Help.")
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        # The planner-1 case has an empty board.  A vision doc is required so
        # the dispatcher has something for the planner to work on after the
        # hard-block reply; without it the loop would exit with "no pending work".
        vision_dir = tmp_path / ".orc" / "vision" / "ready"
        vision_dir.mkdir(parents=True, exist_ok=True)
        (vision_dir / "feature-x.md").write_text("# Feature X\n")

        cases = [
            (
                "planner-1",
                "counter: 1\nopen: []\n",
                {},
            ),
            (
                "coder-1",
                "counter: 1\nopen:\n  - name: 0001-foo.md\n",
                {
                    "_feature_branch_exists": False,
                    "_feature_has_commits_ahead_of_main": False,
                    "_feature_merged_into_dev": False,
                },
            ),
            (
                "qa-1",
                "counter: 1\nopen:\n  - name: 0001-foo.md\n    status: review\n",
                {
                    "_feature_branch_exists": True,
                    "_feature_has_commits_ahead_of_main": True,
                    "_feature_merged_into_dev": False,
                },
            ),
        ]

        for agent_id, board_content, git_map in cases:
            board = tmp_path / ".orc" / "work" / "board.yaml"
            board.parent.mkdir(parents=True, exist_ok=True)
            board.write_text(board_content)
            monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), orc_dir=tmp_path / ".orc"))
            monkeypatch.setattr(_git, "_ensure_feature_worktree", lambda task: tmp_path)
            monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)
            for attr, val in git_map.items():
                monkeypatch.setattr(_git, attr, lambda _b, v=val: v)

            blocked = self._blocked_msgs(agent_id)
            msg_iter = iter([blocked, []])
            monkeypatch.setattr(tg, "get_messages", lambda: next(msg_iter))

            invocations: list[str] = []
            monkeypatch.setattr(
                _ctx,
                "build_agent_context",
                lambda name, msgs, **kw: invocations.append(name) or ("model", "ctx"),
            )

            runner.invoke(m.app, ["run", "--maxcalls", "1"])
            expected_role = agent_id.split("-")[0]
            assert invocations == [expected_role], (
                f"Expected {expected_role} to be dispatched for {agent_id} block"
            )

    def test_timeout_posts_telegram_message_and_exits(
        self, monkeypatch, tmp_path, mock_git, mock_spawn, board_file
    ):
        """timeout during wait_for_human_reply posts a message and exits."""
        from dataclasses import replace as _replace

        import orc.git.core as _git

        board_file("counter: 1\nopen:\n  - name: 0001-foo.md\n")
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), orc_dir=tmp_path / ".orc"))
        monkeypatch.setattr(_git, "_feature_branch_exists", lambda b: False)
        monkeypatch.setattr(_git, "_feature_has_commits_ahead_of_main", lambda b: False)
        monkeypatch.setattr(_git, "_feature_merged_into_dev", lambda b: False)
        monkeypatch.setattr(_git, "_ensure_feature_worktree", lambda task: tmp_path)
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)

        import orc.messaging.telegram as tg

        monkeypatch.setattr(tg, "get_messages", lambda: self._blocked_msgs("coder-1"))
        sent: list[str] = []
        monkeypatch.setattr(tg, "send_message", lambda t: sent.append(t))
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])
        monkeypatch.setattr(_git_mod, "_rebase_dev_on_main", lambda *_: None)
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        def _timeout(msgs, **kw):
            raise TimeoutError("timed out")

        monkeypatch.setattr(_ctx, "wait_for_human_reply", _timeout)

        rc = runner.invoke(m.app, ["run", "--maxcalls", "1"])
        assert rc.exit_code == 1
        assert len(sent) == 1
        assert "(blocked)" in sent[0]
        assert "Stopped" in sent[0]

    def test_planner_done_is_not_blocked(
        self, monkeypatch, tmp_path, mock_git, mock_spawn, board_file
    ):
        """planner(done) is not a blocked state — git routes to planner (no tasks)."""
        from dataclasses import replace as _replace

        import orc.git.core as _git

        board_file("counter: 1\nopen: []\n")
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
            lambda name, msgs, **kw: invocations.append(name) or ("model", "ctx"),
        )
        monkeypatch.setattr(
            inv,
            "spawn",
            lambda *a, **kw: SpawnResult(process=FakePopen(), log_fh=None, context_tmp=""),
        )

        wait_called: list[bool] = []
        monkeypatch.setattr(
            _ctx, "wait_for_human_reply", lambda msgs, **kw: wait_called.append(True) or ""
        )

        rc = runner.invoke(m.app, ["run", "--maxcalls", "1"])
        _ = rc
        assert not wait_called
        assert invocations == ["planner"]
