"""Tests for orc/dispatcher.py — boot message timing."""

from __future__ import annotations

from conftest import FakePopen
from typer.testing import CliRunner

import orc.ai.invoke as inv
import orc.engine.context as _ctx
import orc.engine.dispatcher as _disp
import orc.main as m
from orc.ai.backends import SpawnResult

runner = CliRunner()


class TestBootMessageSentBeforeInvoke:
    def test_boot_message_sent(
        self,
        tmp_path,
        monkeypatch,
        mock_git,
        mock_telegram,
        mock_spawn,
        board_file,
        mock_validate_env,
        mock_rebase,
    ):
        """Orchestrator sends (boot) message before invoking the agent."""
        board_file("counter: 1\ntasks:\n  - name: 0001-foo.md\n")
        monkeypatch.setattr(_ctx, "build_agent_context", lambda *a, **kw: ("system", "user"))
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        result = runner.invoke(m.app, ["run", "--maxcalls", "1"])
        assert result.exit_code == 0
        assert len(mock_telegram) == 1
        assert "(boot)" in mock_telegram[0]
        assert "work/0001-foo.md" in mock_telegram[0]

    def test_boot_message_precedes_invoke(
        self,
        tmp_path,
        monkeypatch,
        mock_git,
        mock_telegram,
        mock_spawn,
        board_file,
        mock_validate_env,
        mock_rebase,
    ):
        """Boot message must be sent BEFORE spawn is called."""
        board_file("counter: 1\ntasks:\n  - name: 0001-foo.md\n")
        call_order: list[str] = []

        def fake_send(text):
            call_order.append("send")

        def fake_get(limit=100):
            return []

        monkeypatch.setattr(_ctx, "build_agent_context", lambda *a, **kw: ("system", "user"))
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        # Override the telegram mocks with our custom tracking
        import orc.messaging.telegram as tg

        monkeypatch.setattr(tg, "_send_message", fake_send)
        monkeypatch.setattr(tg, "_get_messages", fake_get)

        def fake_spawn(*a, **kw):
            call_order.append("invoke")
            return SpawnResult(process=FakePopen(), log_fh=None, context_tmp="")

        monkeypatch.setattr(inv, "spawn", fake_spawn)

        runner.invoke(m.app, ["run", "--maxcalls", "1"])
        assert call_order == ["send", "invoke"]
