"""Telegram Bot API client for the orc agent communication channel.

Agents use this module to read and write messages instead of ``.orc/chat.md``.
The bot token and target chat ID are loaded from a ``.env`` file
(auto-discovered from the current working directory upward).

Required environment variables (optional — Telegram is disabled when absent)::

    COLONY_TELEGRAM_TOKEN   – Bot token from @BotFather
    COLONY_TELEGRAM_CHAT_ID – Target chat/group/channel ID (numeric or @handle)

When Telegram is not configured:

* ``send_message`` writes only to the local log (no HTTP call).
* ``get_messages`` returns only the local log (no incoming updates fetched).
* Hard-blocked agents cannot be resolved via human reply; the dispatcher will
  log a warning and exit rather than waiting indefinitely.

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
from datetime import UTC, datetime
from pathlib import Path

import certifi
import httpx
import structlog
from dotenv import load_dotenv

# Pure message parsing helpers — no side effects, no global state.
# Re-exported here so callers can use ``from orc import telegram as tg``
# and call ``tg.parse_agent_id`` / ``tg.KNOWN_ROLES`` etc. as before.
from orc.messaging.messages import (  # noqa: F401
    _AGENT_ID_RE,
    _MSG_RE,
    INFORMATIONAL_STATES,
    KNOWN_ROLES,
    format_agent_message,
    is_agent_message,
    make_agent_id,
    messages_to_text,
    parse_agent_id,
    parse_last_agent_message,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()  # auto-discovers .env from CWD upward

logger = structlog.get_logger(__name__)

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


def is_configured() -> bool:
    """Return True if both Telegram env vars are present."""
    return bool(_TOKEN and _CHAT_ID)


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
            logger.debug("telegram: skipping malformed JSONL line", line=line, exc_info=True)
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

    The local log write always happens so the local state machine has the
    message even when Telegram is not configured or the API call fails.
    When Telegram is not configured the function returns an empty dict.
    Raises ``httpx.HTTPStatusError`` on Telegram API errors.
    """
    _append_to_log(text)
    if not is_configured():
        return {}
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
    if not is_configured():
        return local
    try:
        remote = _get_telegram_updates(limit)
    except (OSError, Exception):
        logger.debug("get_messages: failed to fetch Telegram updates", exc_info=True)
        remote = []

    seen_texts = {m["text"] for m in local}
    for msg in remote:
        if msg.get("text") not in seen_texts:
            local.append(msg)

    local.sort(key=lambda m: m.get("date", 0))
    return local
