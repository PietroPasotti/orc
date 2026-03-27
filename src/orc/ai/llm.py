"""LLM client abstraction — wraps the ``openai`` SDK for multi-provider support.

Provider presets
----------------
``gemini`` (default)
    Endpoint: ``https://generativelanguage.googleapis.com/v1beta/openai``
    Auth: ``GEMINI_API_TOKEN`` environment variable.

``github``
    Endpoint: ``https://api.github.com/models``
    Auth: GitHub token chain (``GH_TOKEN`` → ``~/.config/github-copilot/apps.json``
    → ``gh auth token``).

``openai``
    Endpoint: default OpenAI (``https://api.openai.com/v1``)
    Auth: ``OPENAI_API_KEY`` environment variable.

All providers expose an OpenAI-compatible chat completions API, so a single
``openai.OpenAI`` client is used for all of them.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
from openai import OpenAI
from openai.types.chat import ChatCompletion

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Provider presets
# ---------------------------------------------------------------------------

_PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "env_key": "GEMINI_API_TOKEN",
    },
    "github": {
        "base_url": "https://api.github.com/models",
        "env_key": "GH_TOKEN",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "env_key": "OPENAI_API_KEY",
    },
}

DEFAULT_PROVIDER = "gemini"
DEFAULT_MODEL = "gemini-2.5-pro"

# Transient HTTP status codes that trigger a retry.
_RETRIABLE_STATUSES = frozenset({429, 500, 502, 503})
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0  # seconds, exponential backoff


# ---------------------------------------------------------------------------
# Token resolution helpers
# ---------------------------------------------------------------------------

_COPILOT_APPS_JSON = Path.home() / ".config" / "github-copilot" / "apps.json"


def resolve_github_token() -> str:
    """Return a GitHub token using the standard fallback chain.

    Resolution order:
    1. ``GH_TOKEN`` environment variable.
    2. ``~/.config/github-copilot/apps.json``.
    3. ``gh auth token``.

    Raises :class:`OSError` if no token is found.
    """
    token = os.environ.get("GH_TOKEN", "").strip()
    if token:
        return token

    if _COPILOT_APPS_JSON.exists():
        try:
            data = json.loads(_COPILOT_APPS_JSON.read_text())
            entry = next(iter(data.values()))
            t = str(entry.get("oauth_token", ""))
            if t:
                return t
        except Exception:
            pass

    try:
        result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True)
        t = result.stdout.strip()
        if t:
            return t
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    raise OSError(
        "No GitHub token found.  Either:\n"
        "  • Set GH_TOKEN in your .env file, or\n"
        "  • Run `gh auth login` to authenticate."
    )


def _resolve_api_key(provider: str) -> str:
    """Resolve the API key for *provider*.

    For ``github`` uses the multi-source GitHub token chain.
    For others reads the environment variable from the provider preset.
    """
    if provider == "github":
        return resolve_github_token()
    preset = _PROVIDER_PRESETS.get(provider)
    if not preset:
        raise ValueError(f"Unknown provider: {provider!r}")
    env_key = preset["env_key"]
    key = os.environ.get(env_key, "").strip()
    if not key:
        raise OSError(
            f"API key for provider {provider!r} not found.\nSet {env_key} in your .env file."
        )
    return key


# ---------------------------------------------------------------------------
# Chat response wrapper
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """A single tool call from the LLM response."""

    id: str
    name: str
    arguments: str  # JSON string


@dataclass
class ChatResponse:
    """Simplified wrapper around a chat completion response."""

    content: str | None
    """Text content of the assistant message, or ``None`` if tool calls only."""

    tool_calls: list[ToolCall]
    """Tool calls requested by the model (empty list if none)."""

    finish_reason: str
    """Why the model stopped: ``stop``, ``tool_calls``, ``length``, etc."""

    usage: dict[str, int] = field(default_factory=dict)
    """Token usage: ``prompt_tokens``, ``completion_tokens``, ``total_tokens``."""

    raw: ChatCompletion | None = None
    """The raw OpenAI SDK response object."""

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


# ---------------------------------------------------------------------------
# LLM Client
# ---------------------------------------------------------------------------


class LLMClient:
    """Thin wrapper around the ``openai`` SDK with provider presets.

    Parameters
    ----------
    provider:
        One of ``"gemini"``, ``"github"``, ``"openai"``.
    model:
        Model name (e.g. ``"gemini-2.5-pro"``, ``"gpt-4o"``).
    api_key:
        Override the API key (skips env-var resolution if given).
    base_url:
        Override the base URL (skips provider preset if given).
    """

    def __init__(
        self,
        provider: str = DEFAULT_PROVIDER,
        model: str = DEFAULT_MODEL,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.provider = provider
        self.model = model

        preset = _PROVIDER_PRESETS.get(provider, {})
        self._base_url = base_url or preset.get("base_url", "https://api.openai.com/v1")
        self._api_key = api_key or _resolve_api_key(provider)

        self._client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
        )

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> ChatResponse:
        """Send a chat completion request with optional tool definitions.

        Parameters
        ----------
        response_format:
            When set, constrains the model's output format.  Common values:

            * ``{"type": "json_object"}`` — model must emit valid JSON.
            * ``{"type": "json_schema", "json_schema": {…}}`` — stricter
              schema-constrained JSON (provider support varies).

        Retries on transient errors with exponential backoff.
        """
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if response_format is not None:
            kwargs["response_format"] = response_format

        response = self._call_with_retry(**kwargs)
        return self._parse_response(response)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call_with_retry(self, **kwargs: Any) -> ChatCompletion:
        """Call the API with exponential backoff on transient errors."""
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return self._client.chat.completions.create(**kwargs)
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                if status in _RETRIABLE_STATUSES and attempt < _MAX_RETRIES:
                    delay = _RETRY_BASE_DELAY * (2**attempt)
                    logger.warning(
                        "llm_retry",
                        attempt=attempt + 1,
                        status=status,
                        delay=delay,
                    )
                    time.sleep(delay)
                    last_exc = exc
                    continue
                raise
        raise last_exc  # type: ignore[misc]  # unreachable but mypy needs it

    @staticmethod
    def _parse_response(response: ChatCompletion) -> ChatResponse:
        """Convert an OpenAI SDK response into our simplified wrapper."""
        choice = response.choices[0]
        msg = choice.message

        tool_calls: list[ToolCall] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=tc.function.arguments,
                    )
                )

        usage: dict[str, int] = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return ChatResponse(
            content=msg.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "unknown",
            usage=usage,
            raw=response,
        )
