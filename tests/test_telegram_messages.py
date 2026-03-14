"""Tests for orc/telegram_messages.py — pure message parsing helpers."""

from __future__ import annotations

import pytest

from orc.messaging.messages import (
    INFORMATIONAL_STATES,
    KNOWN_ROLES,
    format_agent_message,
    is_agent_message,
    make_agent_id,
    messages_to_text,
    parse_agent_id,
)


class TestKnownRoles:
    def test_contains_expected_roles(self):
        assert "planner" in KNOWN_ROLES
        assert "coder" in KNOWN_ROLES
        assert "qa" in KNOWN_ROLES

    def test_informational_states_contains_boot(self):
        assert "boot" in INFORMATIONAL_STATES


class TestParseAgentId:
    @pytest.mark.parametrize(
        "agent_id,expected_role,expected_num",
        [
            ("coder-1", "coder", 1),
            ("qa-2", "qa", 2),
            ("planner-10", "planner", 10),
            ("reviewer-1", None, None),  # unknown role
            ("coder", None, None),  # bare role
            ("", None, None),  # empty string
        ],
    )
    def test_parse_agent_id(self, agent_id, expected_role, expected_num):
        assert parse_agent_id(agent_id) == (expected_role, expected_num)


class TestMakeAgentId:
    @pytest.mark.parametrize(
        "role,num,expected",
        [
            ("coder", 1, "coder-1"),
            ("qa", 3, "qa-3"),
            ("planner", 99, "planner-99"),
        ],
    )
    def test_make_agent_id(self, role, num, expected):
        assert make_agent_id(role, num) == expected

    def test_invalid_role_raises(self):
        with pytest.raises(ValueError, match="Unknown role"):
            make_agent_id("reviewer", 1)


class TestIsAgentMessage:
    @pytest.mark.parametrize(
        "message,is_valid",
        [
            ("[coder-1](ready) 2026-01-01T00:00:00Z: Done.", True),
            ("[reviewer-1](ready) 2026-01-01T00:00:00Z: Done.", False),  # unknown role
            ("hello world", False),  # bare text
            ("", False),  # empty
        ],
    )
    def test_is_agent_message(self, message, is_valid):
        assert is_agent_message(message) == is_valid


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

    @pytest.mark.parametrize(
        "msg,expected_name",
        [
            ({"text": "hi", "date": 0, "from": {"first_name": "Eve"}}, "Eve"),
            ({"text": "hi", "date": 0, "from": {}}, "unknown"),
        ],
    )
    def test_name_fallbacks(self, msg, expected_name):
        text = messages_to_text([msg])
        assert expected_name in text
