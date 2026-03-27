"""Pure message-parsing helpers for the orc Telegram channel.

This module contains **only** stateless functions and constants — no file I/O,
no HTTP calls, no global mutable state.  It is imported by :mod:`orc.messaging.telegram`
and may also be imported independently for testing or tooling that only needs
message parsing.

Public symbols (all re-exported from :mod:`orc.messaging.telegram`):

* :data:`KNOWN_ROLES` — frozenset of valid agent role names
* :data:`INFORMATIONAL_STATES` — states that do not drive state-machine transitions
* :class:`ChatMessage` — immutable representation of a chat message
* :func:`parse_agent_id` — ``"{role}-{n}"`` → ``(role, n)``
* :func:`make_agent_id` — ``(role, n)`` → ``"{role}-{n}"``
* :func:`is_agent_message` — test if a text string is a formatted agent message
* :func:`format_agent_message` — build a formatted agent message string
* :func:`messages_to_text` — render a message list as a plain-text chat log
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from orc.squad import AgentRole

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The set of valid agent *roles*.  Agent messages use IDs of the form
# ``{role}-{n}`` (e.g. ``coder-1``, ``qa-2``).
KNOWN_ROLES: frozenset[AgentRole] = frozenset(AgentRole)

# States that are informational only — not used for state-machine transitions.
# A boot message never stalls the workflow.
INFORMATIONAL_STATES: frozenset[str] = frozenset({"boot"})

# Matches: [name](state) 2026-03-01T10:00:00Z: message
_MSG_RE = re.compile(r"^\[([^\]]+)\]\(([^)]+)\)\s+\S+:\s+.*$")

# Matches the agent-ID convention: ``{role}-{n}`` where n >= 1.
_AGENT_ID_RE = re.compile(r"^([a-z]+)-(\d+)$")


# ---------------------------------------------------------------------------
# Chat message type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChatMessage:
    """Immutable representation of a chat message (local log or Telegram API)."""

    text: str
    """Message body."""
    date: int
    """Unix timestamp."""
    sender_name: str
    """Display name of the sender (username or first_name)."""

    # FIXME: add a __repr__ showing sender and truncated text
    # e.g. "ChatMessage(alice, 'Fix the bug in...')"
    # Truncate text to 30 chars with ellipsis if longer.


# ---------------------------------------------------------------------------
# Agent ID helpers
# ---------------------------------------------------------------------------


def parse_agent_id(agent_id: str) -> tuple[str, int] | tuple[None, None]:
    """Parse an agent ID into ``(role, n)``.

    Returns ``(None, None)`` for unrecognised formats.

    Examples::

        parse_agent_id("coder-1")    # → ("coder", 1)
        parse_agent_id("qa-2")       # → ("qa", 2)
        parse_agent_id("coder")      # → (None, None)  — old format
        parse_agent_id("reviewer-1") # → (None, None)  — unknown role
    """
    m = _AGENT_ID_RE.match(agent_id)
    if m and m.group(1) in KNOWN_ROLES:
        return m.group(1), int(m.group(2))
    return None, None


def make_agent_id(role: str, n: int) -> str:
    """Build an agent ID from *role* and *n*.

    Examples::

        make_agent_id("coder", 1)  # → "coder-1"
        make_agent_id("qa", 3)     # → "qa-3"
    """
    if role not in KNOWN_ROLES:
        raise ValueError(f"Unknown role {role!r}. Valid roles: {sorted(KNOWN_ROLES)}")
    return f"{role}-{n}"


# ---------------------------------------------------------------------------
# Message format helpers
# ---------------------------------------------------------------------------


def format_agent_message(agent_name: str, state: str, body: str) -> str:
    """Build a properly formatted agent message string ready to send."""
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"[{agent_name}]({state}) {ts}: {body}"


def is_agent_message(text: str) -> bool:
    """Return True if *text* is a formatted message from a known agent."""
    m = _MSG_RE.match(text.strip())
    if not m:
        return False
    role, _ = parse_agent_id(m.group(1))
    return role is not None


def messages_to_text(messages: list[ChatMessage]) -> str:
    """Render *messages* as a plain-text chat log for inclusion in agent context."""
    lines: list[str] = []
    for msg in messages:
        ts = datetime.fromtimestamp(msg.date, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines.append(f"[{ts}] {msg.sender_name}: {msg.text}")
    return "\n".join(lines) if lines else "_No messages yet._"
