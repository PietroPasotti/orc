"""Tests for orc/telegram_messages.py — pure message parsing helpers."""

from __future__ import annotations

import pytest

from orc.messaging.messages import (
    INFORMATIONAL_STATES,
    KNOWN_AGENTS,
    KNOWN_ROLES,
    format_agent_message,
    is_agent_message,
    make_agent_id,
    messages_to_text,
    parse_agent_id,
    parse_last_agent_message,
)


class TestKnownRoles:
    def test_contains_expected_roles(self):
        assert "planner" in KNOWN_ROLES
        assert "coder" in KNOWN_ROLES
        assert "qa" in KNOWN_ROLES

    def test_known_agents_is_alias(self):
        assert KNOWN_AGENTS is KNOWN_ROLES

    def test_informational_states_contains_boot(self):
        assert "boot" in INFORMATIONAL_STATES


class TestParseAgentId:
    def test_valid_coder(self):
        assert parse_agent_id("coder-1") == ("coder", 1)

    def test_valid_qa(self):
        assert parse_agent_id("qa-2") == ("qa", 2)

    def test_valid_planner(self):
        assert parse_agent_id("planner-10") == ("planner", 10)

    def test_unknown_role(self):
        assert parse_agent_id("reviewer-1") == (None, None)

    def test_bare_role(self):
        assert parse_agent_id("coder") == (None, None)

    def test_empty_string(self):
        assert parse_agent_id("") == (None, None)


class TestMakeAgentId:
    def test_basic(self):
        assert make_agent_id("coder", 1) == "coder-1"
        assert make_agent_id("qa", 3) == "qa-3"
        assert make_agent_id("planner", 99) == "planner-99"

    def test_invalid_role_raises(self):
        with pytest.raises(ValueError, match="Unknown role"):
            make_agent_id("reviewer", 1)


class TestIsAgentMessage:
    def test_recognises_valid_message(self):
        assert is_agent_message("[coder-1](ready) 2026-01-01T00:00:00Z: Done.")

    def test_rejects_unknown_role(self):
        assert not is_agent_message("[reviewer-1](ready) 2026-01-01T00:00:00Z: Done.")

    def test_rejects_bare_text(self):
        assert not is_agent_message("hello world")

    def test_rejects_empty(self):
        assert not is_agent_message("")


class TestFormatAgentMessage:
    def test_format_contains_name_and_state(self):
        msg = format_agent_message("coder-1", "ready", "Done.")
        assert msg.startswith("[coder-1](ready)")
        assert "Done." in msg

    def test_format_includes_timestamp(self):
        msg = format_agent_message("qa-1", "passed", "All green.")
        # Should have a Z-terminated UTC timestamp
        assert "Z:" in msg


class TestMessagesToText:
    def test_formats_messages(self):
        msgs = [
            {"text": "hello", "date": 1700000000, "from": {"username": "alice"}},
            {"text": "world", "date": 1700000060, "from": {"username": "bob"}},
        ]
        text = messages_to_text(msgs)
        assert "alice" in text
        assert "hello" in text
        assert "bob" in text
        assert "world" in text

    def test_empty_returns_placeholder(self):
        assert messages_to_text([]) == "_No messages yet._"

    def test_uses_first_name_when_username_missing(self):
        msgs = [{"text": "hi", "date": 0, "from": {"first_name": "Eve"}}]
        text = messages_to_text(msgs)
        assert "Eve" in text

    def test_falls_back_to_unknown_when_no_name(self):
        msgs = [{"text": "hi", "date": 0, "from": {}}]
        text = messages_to_text(msgs)
        assert "unknown" in text


class TestParseLastAgentMessage:
    def test_returns_last_terminal_state(self):
        msgs = [
            {"text": "[coder-1](boot) 2026-01-01T00:00:00Z: Starting.", "date": 1},
            {"text": "[coder-1](ready) 2026-01-01T00:01:00Z: Done.", "date": 2},
        ]
        agent, state = parse_last_agent_message(msgs)
        assert agent == "coder-1"
        assert state == "ready"

    def test_skips_informational_states(self):
        msgs = [
            {"text": "[qa-1](done) 2026-01-01T00:00:00Z: QA passed.", "date": 1},
            {"text": "[coder-1](boot) 2026-01-01T00:01:00Z: Starting.", "date": 2},
        ]
        agent, state = parse_last_agent_message(msgs)
        assert agent == "qa-1"
        assert state == "done"

    def test_returns_none_when_no_agent_messages(self):
        msgs = [{"text": "Human message", "date": 1, "from": {"username": "human"}}]
        agent, state = parse_last_agent_message(msgs)
        assert agent is None
        assert state is None

    def test_empty_list(self):
        assert parse_last_agent_message([]) == (None, None)
