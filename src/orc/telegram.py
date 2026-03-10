"""Telegram Bot API client for the orc agent communication channel.

Agents use this module to read and write messages instead of ``orc/chat.md``.
The bot token and target chat ID are loaded from a ``.env`` file
(auto-discovered from the current working directory upward).

Required environment variables::

    COLONY_TELEGRAM_TOKEN   – Bot token from @BotFather
    COLONY_TELEGRAM_CHAT_ID – Target chat/group/channel ID (numeric or @handle)

Message format::

    [agent_name](exit_state) YYYY-MM-DDTHH:MM:SSZ: <message text>

Example::

    [planner](ready) 2026-03-01T10:00:00Z: Created plan 0002-add-resource-system.md.

Local log
---------
Because the Telegram Bot API's ``getUpdates`` only returns *incoming* messages
(not messages the bot itself sends), outbound ``send_message`` calls are also
appended to a local JSONL log at ``{AGENTS_DIR}/chat.log`` (i.e. inside the
project's ``.orc/`` config directory).  ``get_messages`` merges both sources
so the state machine always sees the full history.
"""

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import certifi
import httpx
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()  # auto-discovers .env from CWD upward

_TOKEN = os.environ.get("COLONY_TELEGRAM_TOKEN")
_CHAT_ID = os.environ.get("COLONY_TELEGRAM_CHAT_ID")
_API_BASE = f"https://api.telegram.org/bot{_TOKEN}"

# Sentinel — resolved lazily on first use so that config.AGENTS_DIR is fully
# initialised before we compute the path.  Tests may patch this directly.
_LOG_FILE: Path | None = None


def _get_log_file() -> Path:
    """Return the chat log path, creating the file if it does not yet exist."""
    global _LOG_FILE
    if _LOG_FILE is None:
        from orc import config  # import here to avoid circular-import at module level

        _LOG_FILE = config.AGENTS_DIR / "chat.log"
    _LOG_FILE.touch(exist_ok=True)
    return _LOG_FILE


# Use certifi's bundled CA certs so we don't depend on system cert paths
# (which may be absent inside containers).
_CA_BUNDLE = certifi.where()

# The set of valid agent *roles*.  Agent messages now use IDs of the form
# ``{role}-{n}`` (e.g. ``coder-1``, ``qa-2``) rather than bare role names.
KNOWN_ROLES: frozenset[str] = frozenset({"planner", "coder", "qa"})

# Kept for backward compatibility — KNOWN_AGENTS is now an alias for KNOWN_ROLES.
KNOWN_AGENTS = KNOWN_ROLES

# States that are informational only – not used for state-machine transitions.
# parse_last_agent_message skips over messages with these states so they
# never stall the workflow (e.g. if an agent crashes after booting but before
# posting a terminal state, the previous terminal state is still visible).
INFORMATIONAL_STATES = {"boot"}

# Matches: [name](state) 2026-03-01T10:00:00Z: message
_MSG_RE = re.compile(r"^\[([^\]]+)\]\(([^)]+)\)\s+\S+:\s+.*$")

# Matches the agent-ID convention: ``{role}-{n}`` where n >= 1.
_AGENT_ID_RE = re.compile(r"^([a-z]+)-(\d+)$")


def _require_config() -> None:
    """Raise a clear error if the bot token or chat ID is not configured."""
    if not _TOKEN:
        raise OSError(
            "COLONY_TELEGRAM_TOKEN is not set. "
            "Copy .env.example to .env and fill in your bot token."
        )
    if not _CHAT_ID:
        raise OSError(
            "COLONY_TELEGRAM_CHAT_ID is not set. "
            "Copy .env.example to .env and fill in your chat ID."
        )


def _append_to_log(text: str) -> None:
    """Append a message to the local chat.log as a JSONL entry."""
    entry = {
        "text": text,
        "date": int(datetime.now(UTC).timestamp()),
        "from": {"username": "bot", "first_name": "bot"},
    }
    with _get_log_file().open("a") as fh:
        fh.write(json.dumps(entry) + "\n")


def _read_log() -> list[dict]:
    """Return all entries from the local chat.log."""
    log_file = _get_log_file()
    msgs = []
    for line in log_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msgs.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return msgs


def _get_telegram_updates(limit: int = 100) -> list[dict]:
    """Fetch incoming messages via getUpdates (human/external messages only)."""
    _require_config()
    with httpx.Client(timeout=15, verify=_CA_BUNDLE) as client:
        resp = client.get(
            f"{_API_BASE}/getUpdates",
            params={"limit": limit, "allowed_updates": ["message"]},
        )
        resp.raise_for_status()
        data = resp.json()
        return [u["message"] for u in data.get("result", []) if "message" in u]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def send_message(text: str) -> dict:
    """Post *text* to the Telegram chat and record it in the local log.

    The local log write happens first so the state machine always has the
    message even if the Telegram call fails.
    Raises ``httpx.HTTPStatusError`` on Telegram API errors.
    """
    _append_to_log(text)
    _require_config()
    with httpx.Client(timeout=15, verify=_CA_BUNDLE) as client:
        resp = client.post(
            f"{_API_BASE}/sendMessage",
            json={"chat_id": _CHAT_ID, "text": text},
        )
        resp.raise_for_status()
        return resp.json()


def get_messages(limit: int = 100) -> list[dict]:
    """Return the merged message history from the local log and Telegram updates.

    The local log contains all bot-sent messages (agent exit states).
    Telegram updates contain incoming messages from humans or other bots.
    The merged list is sorted by timestamp so ``parse_last_agent_message``
    always sees events in the correct order.
    """
    local = _read_log()
    try:
        remote = _get_telegram_updates(limit)
    except (OSError, Exception):
        remote = []

    seen_texts = {m["text"] for m in local}
    for msg in remote:
        if msg.get("text") not in seen_texts:
            local.append(msg)

    local.sort(key=lambda m: m.get("date", 0))
    return local


# ---------------------------------------------------------------------------
# Message parsing helpers
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


def parse_last_agent_message(
    messages: list[dict],
) -> tuple[str, str] | tuple[None, None]:
    """Scan *messages* from newest to oldest and return ``(agent_id, state)``.

    Agent messages must use the ``{role}-{n}`` ID format (e.g. ``coder-1``).
    Returns ``(None, None)`` when no known-agent message is found.
    """
    for msg in reversed(messages):
        text = msg.get("text", "").strip()
        m = _MSG_RE.match(text)
        if m:
            name, state = m.group(1), m.group(2)
            role, _ = parse_agent_id(name)
            if role is not None and state not in INFORMATIONAL_STATES:
                return name, state
    return None, None


def is_agent_message(text: str) -> bool:
    """Return True if *text* is a formatted message from a known agent."""
    m = _MSG_RE.match(text.strip())
    if not m:
        return False
    role, _ = parse_agent_id(m.group(1))
    return role is not None


def format_agent_message(agent_name: str, state: str, body: str) -> str:
    """Build a properly formatted agent message string ready to send."""
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"[{agent_name}]({state}) {ts}: {body}"


def messages_to_text(messages: list[dict]) -> str:
    """Render *messages* as a plain-text chat log for inclusion in agent context."""
    lines: list[str] = []
    for msg in messages:
        sender = msg.get("from", {})
        name = sender.get("username") or sender.get("first_name", "unknown")
        text = msg.get("text", "")
        date = msg.get("date", 0)
        ts = datetime.fromtimestamp(date, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines.append(f"[{ts}] {name}: {text}")
    return "\n".join(lines) if lines else "_No messages yet._"
