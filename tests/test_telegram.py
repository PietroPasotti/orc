"""Tests for orc/telegram.py."""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest
from conftest import make_msg

from orc.messaging import telegram as tg
from orc.messaging.messages import ChatMessage as _ChatMessage

# ---------------------------------------------------------------------------
# Local chat.log – read/write and get_messages merging
# ---------------------------------------------------------------------------


class TestLocalChatLog:
    """The local log is the fix for Telegram's getUpdates blindspot."""

    def test_append_and_read_log(self, tmp_path):
        log_file = tmp_path / "chat.log"
        with patch.object(tg, "_LOG_FILE", log_file):
            tg._append_to_log("hello world")
            entries = tg._read_log()

        assert len(entries) == 1
        assert entries[0].text == "hello world"
        assert entries[0].date > 0
        assert entries[0].sender_name == "bot"

    def test_read_log_returns_empty_when_missing(self, tmp_path):
        log_file = tmp_path / "nonexistent.log"
        with patch.object(tg, "_LOG_FILE", log_file):
            assert tg._read_log() == []

    def test_read_log_skips_corrupt_lines(self, tmp_path):
        log_file = tmp_path / "chat.log"
        log_file.write_text('{"text": "ok"}\nnot-json\n{"text": "also ok"}\n')
        with patch.object(tg, "_LOG_FILE", log_file):
            entries = tg._read_log()
        assert len(entries) == 2
        assert entries[0].text == "ok"
        assert entries[1].text == "also ok"

    def test_send_message_writes_to_log(self, tmp_path):
        log_file = tmp_path / "chat.log"
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}

        with (
            patch.object(tg, "_LOG_FILE", log_file),
            patch.object(tg, "_is_configured", return_value=True),
            patch("httpx.Client") as mock_client_cls,
        ):
            mock_client = MagicMock()
            mock_client.__enter__ = lambda s: mock_client
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            tg._send_message("[planner-1](ready) 2026-03-09T10:00:00Z: Plan created.")

        lines = [json.loads(ln) for ln in log_file.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        assert lines[0]["text"] == "[planner-1](ready) 2026-03-09T10:00:00Z: Plan created."

    def test_send_message_no_telegram_writes_log_only(self, tmp_path):
        """When Telegram is not configured, _send_message writes to local log only."""
        log_file = tmp_path / "chat.log"
        with (
            patch.object(tg, "_LOG_FILE", log_file),
            patch.object(tg, "_is_configured", return_value=False),
        ):
            tg._send_message("[coder-1](ready) 2026-03-09T10:00:00Z: Done.")
        lines = [json.loads(ln) for ln in log_file.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        assert lines[0]["text"] == "[coder-1](ready) 2026-03-09T10:00:00Z: Done."

    def test_get_messages_merges_log_and_telegram(self, tmp_path):
        log_file = tmp_path / "chat.log"
        bot_entry = {
            "text": "[planner-1](ready) 2026-03-09T10:00:00Z: Plan created.",
            "date": 2000,
            "from": {"username": "bot", "first_name": "bot"},
        }
        log_file.write_text(json.dumps(bot_entry) + "\n")

        human_msg = make_msg("[pietro] 2026-03-09T09:00:00Z: Start!", ts=1000)

        with (
            patch.object(tg, "_LOG_FILE", log_file),
            patch.object(tg, "_is_configured", return_value=True),
            patch.object(tg, "_get_telegram_updates", return_value=[human_msg]),
        ):
            msgs = tg._get_messages()

        assert len(msgs) == 2
        assert msgs[0].date == 1000
        assert msgs[1].date == 2000

    def test_get_messages_no_telegram_returns_log_only(self, tmp_path):
        """When Telegram is not configured, _get_messages returns local log only."""
        log_file = tmp_path / "chat.log"
        entry = {
            "text": "[coder-1](ready) 2026-03-09T10:00:00Z: Done.",
            "date": 1000,
            "from": {"username": "bot", "first_name": "bot"},
        }
        log_file.write_text(json.dumps(entry) + "\n")

        with (
            patch.object(tg, "_LOG_FILE", log_file),
            patch.object(tg, "_is_configured", return_value=False),
        ):
            msgs = tg._get_messages()

        assert len(msgs) == 1
        assert msgs[0].text == "[coder-1](ready) 2026-03-09T10:00:00Z: Done."

    def test_get_messages_deduplicates_by_text(self, tmp_path):
        log_file = tmp_path / "chat.log"
        msg_text = "[planner-1](ready) 2026-03-09T10:00:00Z: Plan created."
        entry = {"text": msg_text, "date": 2000, "from": {"username": "bot", "first_name": "bot"}}
        log_file.write_text(json.dumps(entry) + "\n")

        duplicate = make_msg(msg_text, ts=2000)

        with (
            patch.object(tg, "_LOG_FILE", log_file),
            patch.object(tg, "_is_configured", return_value=True),
            patch.object(tg, "_get_telegram_updates", return_value=[duplicate]),
        ):
            msgs = tg._get_messages()

        assert len(msgs) == 1

    def test_get_messages_falls_back_to_log_when_telegram_unavailable(self, tmp_path):
        log_file = tmp_path / "chat.log"
        entry = {
            "text": "[coder-1](done) 2026-03-09T11:00:00Z: Done.",
            "date": 5000,
            "from": {"username": "bot", "first_name": "bot"},
        }
        log_file.write_text(json.dumps(entry) + "\n")

        with (
            patch.object(tg, "_LOG_FILE", log_file),
            patch.object(tg, "_is_configured", return_value=True),
            patch.object(tg, "_get_telegram_updates", side_effect=Exception("no network")),
        ):
            msgs = tg._get_messages()

        assert len(msgs) == 1
        assert msgs[0].text == "[coder-1](done) 2026-03-09T11:00:00Z: Done."


# ---------------------------------------------------------------------------
# telegram.py coverage gap tests
# ---------------------------------------------------------------------------


def _make_msg(text: str, *, ts: int = 1000) -> _ChatMessage:
    return _ChatMessage(text=text, date=ts, sender_name="agent")


class TestTelegramCoverage:
    def test_require_config_raises_without_token(self, monkeypatch):
        monkeypatch.setattr(tg, "_TOKEN", None)
        monkeypatch.setattr(tg, "_CHAT_ID", "123")
        with pytest.raises(OSError, match="COLONY_TELEGRAM_TOKEN"):
            tg._require_config()

    def test_require_config_raises_without_chat_id(self, monkeypatch):
        monkeypatch.setattr(tg, "_TOKEN", "tok")
        monkeypatch.setattr(tg, "_CHAT_ID", None)
        with pytest.raises(OSError, match="COLONY_TELEGRAM_CHAT_ID"):
            tg._require_config()

    def test_read_log_skips_blank_lines(self, tmp_path, monkeypatch):
        """Line 121: blank lines in chat.log are skipped."""
        log_file = tmp_path / "chat.log"
        log_file.write_text('\n\n{"text":"hello","date":1,"from":{"username":"u"}}\n\n')
        monkeypatch.setattr(tg, "_get_log_file", lambda: log_file)
        msgs = tg._read_log()
        assert len(msgs) == 1
        assert msgs[0].text == "hello"

    def test_parse_chat_message_non_dict_sender(self):
        """Line 132: when 'from' is not a dict, sender_name falls back to 'unknown'."""
        raw: dict[str, object] = {"text": "hello", "date": 1, "from": "not-a-dict"}
        msg = tg._parse_chat_message(raw)
        assert msg.text == "hello"
        assert msg.sender_name == "unknown"

    def test_get_telegram_updates_non_list_result(self, monkeypatch):
        """Line 171: when 'result' is not a list, return empty list."""
        monkeypatch.setattr(tg, "_TOKEN", "tok123")
        monkeypatch.setattr(tg, "_CHAT_ID", "456")
        fake_resp = MagicMock()
        fake_resp.json.return_value = {"result": "unexpected-string"}
        fake_client = MagicMock()
        fake_client.__enter__ = lambda s: fake_client
        fake_client.__exit__ = MagicMock(return_value=False)
        fake_client.get.return_value = fake_resp
        httpx_mod = sys.modules["httpx"]
        orig = httpx_mod.Client
        httpx_mod.Client = MagicMock(return_value=fake_client)
        try:
            msgs = tg._get_telegram_updates()
        finally:
            httpx_mod.Client = orig
        assert msgs == []

    def test_get_telegram_updates_filters_no_message(self, tmp_path, monkeypatch):
        """Lines 132-139: updates without 'message' key are filtered."""
        monkeypatch.setattr(tg, "_TOKEN", "tok123")
        monkeypatch.setattr(tg, "_CHAT_ID", "456")
        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "result": [
                {"message": {"text": "hi", "date": 1, "from": {"username": "u"}}},
                {"update_id": 1},  # no "message" key → skipped
            ]
        }
        fake_client = MagicMock()
        fake_client.__enter__ = lambda s: fake_client
        fake_client.__exit__ = MagicMock(return_value=False)
        fake_client.get.return_value = fake_resp
        httpx_mod = sys.modules["httpx"]
        orig = httpx_mod.Client
        httpx_mod.Client = MagicMock(return_value=fake_client)
        try:
            msgs = tg._get_telegram_updates()
        finally:
            httpx_mod.Client = orig
        assert len(msgs) == 1
        assert msgs[0].text == "hi"

    def test_make_agent_id_invalid_role_raises(self):
        with pytest.raises(ValueError, match="Unknown role"):
            tg.make_agent_id("wizard", 1)

    def test_is_configured_true(self, monkeypatch):
        monkeypatch.setattr(tg, "_TOKEN", "tok")
        monkeypatch.setattr(tg, "_CHAT_ID", "123")
        assert tg._is_configured() is True

    def test_is_configured_false_no_token(self, monkeypatch):
        monkeypatch.setattr(tg, "_TOKEN", None)
        monkeypatch.setattr(tg, "_CHAT_ID", "123")
        assert tg._is_configured() is False

    def test_is_agent_message_true(self):
        assert tg.is_agent_message("[coder-1](done) 2026-01-01T00:00:00Z: Done.")

    def test_is_agent_message_false(self):
        assert not tg.is_agent_message("just a plain message")

    def test_format_agent_message_and_parse(self):
        """Lines 220: format_agent_message builds parseable string."""
        msg = tg.format_agent_message("coder-1", "done", "Task complete.")
        assert "coder-1" in msg
        assert "done" in msg

    def test_get_log_file_uses_log_dir(self, tmp_path, monkeypatch):
        """_get_log_file returns a path inside log_dir, not orc_dir."""
        from dataclasses import replace as _replace

        import orc.config as _cfg

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), log_dir=log_dir))
        monkeypatch.setattr(tg, "_LOG_FILE", None)
        result = tg._get_log_file()
        assert result == log_dir / "chat.log"


# ---------------------------------------------------------------------------
# TelegramMessagingService — public API surface
# ---------------------------------------------------------------------------


class TestTelegramMessagingService:
    def test_is_configured_delegates_to_private(self, monkeypatch):
        monkeypatch.setattr(tg, "_TOKEN", "tok")
        monkeypatch.setattr(tg, "_CHAT_ID", "chat")
        assert tg.TelegramMessagingService().is_configured() is True
        monkeypatch.setattr(tg, "_TOKEN", None)
        assert tg.TelegramMessagingService().is_configured() is False

    def test_send_message_delegates_to_private(self, tmp_path, monkeypatch):
        sent: list[str] = []
        monkeypatch.setattr(tg, "_send_message", lambda text: sent.append(text))
        tg.TelegramMessagingService().send_message("hello")
        assert sent == ["hello"]

    def test_get_messages_delegates_to_private(self, monkeypatch):
        msgs = [tg.ChatMessage(text="hi", date=1, sender_name="u")]
        monkeypatch.setattr(tg, "_get_messages", lambda limit=100: msgs)
        result = tg.TelegramMessagingService().get_messages()
        assert result == msgs

    def test_post_boot_message_sends_formatted(self, monkeypatch):
        sent: list[str] = []
        monkeypatch.setattr(tg, "_send_message", lambda text: sent.append(text))
        tg.TelegramMessagingService().post_boot_message("coder-1", "Starting up.")
        assert len(sent) == 1
        assert "coder-1" in sent[0]
        assert "boot" in sent[0]
