"""Tests for :mod:`orc.ai.llm` — LLM client abstraction."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orc.ai.llm import (
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    ChatResponse,
    LLMClient,
    ToolCall,
    _resolve_api_key,
    resolve_github_token,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_completion(
    content: str | None = "hello",
    tool_calls: list[Any] | None = None,
    finish_reason: str = "stop",
    usage: dict[str, int] | None = None,
) -> Any:
    """Build a fake ChatCompletion-like object."""
    tc_objs = None
    if tool_calls:
        tc_objs = []
        for tc in tool_calls:
            fn = SimpleNamespace(name=tc["name"], arguments=tc["arguments"])
            tc_objs.append(SimpleNamespace(id=tc["id"], function=fn, type="function"))

    msg = SimpleNamespace(content=content, tool_calls=tc_objs)
    choice = SimpleNamespace(message=msg, finish_reason=finish_reason, index=0)

    usage_obj = None
    if usage:
        usage_obj = SimpleNamespace(**usage)

    return SimpleNamespace(choices=[choice], usage=usage_obj)


# ---------------------------------------------------------------------------
# Provider presets
# ---------------------------------------------------------------------------


class TestProviderPresets:
    def test_default_provider_is_gemini(self) -> None:
        assert DEFAULT_PROVIDER == "gemini"

    def test_default_model_is_gemini_flash(self) -> None:
        assert DEFAULT_MODEL == "gemini-2.5-pro"


# ---------------------------------------------------------------------------
# API key resolution
# ---------------------------------------------------------------------------


class TestResolveApiKey:
    def test_gemini_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GEMINI_API_TOKEN", "test-key-123")
        assert _resolve_api_key("gemini") == "test-key-123"

    def test_openai_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert _resolve_api_key("openai") == "sk-test"

    def test_missing_env_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GEMINI_API_TOKEN", raising=False)
        with pytest.raises(OSError, match="GEMINI_API_TOKEN"):
            _resolve_api_key("gemini")

    def test_unknown_provider_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown provider"):
            _resolve_api_key("nonexistent")

    def test_github_delegates_to_resolve_github_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GH_TOKEN", "ghp_test")
        assert _resolve_api_key("github") == "ghp_test"


# ---------------------------------------------------------------------------
# GitHub token resolution
# ---------------------------------------------------------------------------


class TestResolveGithubToken:
    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GH_TOKEN", "ghp_from_env")
        assert resolve_github_token() == "ghp_from_env"

    def test_from_apps_json(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        monkeypatch.delenv("GH_TOKEN", raising=False)
        apps = tmp_path / "apps.json"
        apps.write_text('{"default": {"oauth_token": "ghp_from_file"}}')
        with patch("orc.ai.llm._COPILOT_APPS_JSON", apps):
            assert resolve_github_token() == "ghp_from_file"

    def test_from_gh_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GH_TOKEN", raising=False)
        with (
            patch("orc.ai.llm._COPILOT_APPS_JSON", MagicMock(exists=lambda: False)),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = SimpleNamespace(stdout="ghp_from_cli\n", returncode=0)
            assert resolve_github_token() == "ghp_from_cli"

    def test_no_token_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GH_TOKEN", raising=False)
        with (
            patch("orc.ai.llm._COPILOT_APPS_JSON", MagicMock(exists=lambda: False)),
            patch("subprocess.run", side_effect=FileNotFoundError),
        ):
            with pytest.raises(OSError, match="No GitHub token"):
                resolve_github_token()


# ---------------------------------------------------------------------------
# LLMClient construction
# ---------------------------------------------------------------------------


class TestLLMClientInit:
    def test_default_provider_and_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GEMINI_API_TOKEN", "test-key")
        client = LLMClient()
        assert client.provider == "gemini"
        assert client.model == "gemini-2.5-pro"

    def test_explicit_api_key_skips_resolution(self) -> None:
        client = LLMClient(api_key="explicit-key")
        assert client._api_key == "explicit-key"

    def test_explicit_base_url_overrides_preset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GEMINI_API_TOKEN", "test-key")
        client = LLMClient(base_url="https://custom.example.com/v1")
        assert client._base_url == "https://custom.example.com/v1"

    def test_github_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GH_TOKEN", "ghp_test")
        client = LLMClient(provider="github", model="gpt-4o")
        assert client.provider == "github"
        assert client.model == "gpt-4o"
        assert "github.com" in client._base_url


# ---------------------------------------------------------------------------
# LLMClient.chat
# ---------------------------------------------------------------------------


class TestLLMClientChat:
    @pytest.fixture()
    def client(self) -> LLMClient:
        return LLMClient(api_key="fake-key", provider="gemini")

    def test_simple_text_response(self, client: LLMClient) -> None:
        completion = _fake_completion(
            content="Hello world",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
        with patch.object(client._client.chat.completions, "create", return_value=completion):
            resp = client.chat([{"role": "user", "content": "hi"}])
        assert isinstance(resp, ChatResponse)
        assert resp.content == "Hello world"
        assert not resp.has_tool_calls
        assert resp.finish_reason == "stop"
        assert resp.usage["total_tokens"] == 15

    def test_tool_call_response(self, client: LLMClient) -> None:
        completion = _fake_completion(
            content=None,
            tool_calls=[
                {
                    "id": "call_1",
                    "name": "read_file",
                    "arguments": '{"path": "/tmp/test.txt"}',
                }
            ],
            finish_reason="tool_calls",
        )
        with patch.object(client._client.chat.completions, "create", return_value=completion):
            resp = client.chat(
                [{"role": "user", "content": "read file"}],
                tools=[{"type": "function", "function": {"name": "read_file"}}],
            )
        assert resp.has_tool_calls
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "read_file"
        assert resp.tool_calls[0].id == "call_1"
        assert resp.finish_reason == "tool_calls"

    def test_multiple_tool_calls(self, client: LLMClient) -> None:
        completion = _fake_completion(
            content=None,
            tool_calls=[
                {"id": "call_1", "name": "read_file", "arguments": '{"path": "a.py"}'},
                {"id": "call_2", "name": "read_file", "arguments": '{"path": "b.py"}'},
            ],
            finish_reason="tool_calls",
        )
        with patch.object(client._client.chat.completions, "create", return_value=completion):
            resp = client.chat([{"role": "user", "content": "read"}])
        assert len(resp.tool_calls) == 2

    def test_passes_temperature_and_max_tokens(self, client: LLMClient) -> None:
        completion = _fake_completion()
        with patch.object(
            client._client.chat.completions, "create", return_value=completion
        ) as mock:
            client.chat([{"role": "user", "content": "hi"}], temperature=0.5, max_tokens=100)
        _, kwargs = mock.call_args
        assert kwargs["temperature"] == 0.5
        assert kwargs["max_tokens"] == 100

    def test_no_usage_in_response(self, client: LLMClient) -> None:
        completion = _fake_completion(usage=None)
        with patch.object(client._client.chat.completions, "create", return_value=completion):
            resp = client.chat([{"role": "user", "content": "hi"}])
        assert resp.usage == {}


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------


class TestRetryLogic:
    def test_retries_on_429(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("orc.ai.llm._RETRY_BASE_DELAY", 0.01)
        client = LLMClient(api_key="fake-key")
        exc = Exception("rate limited")
        exc.status_code = 429  # type: ignore[attr-defined]
        completion = _fake_completion()

        call_count = 0

        def side_effect(**kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise exc
            return completion

        with patch.object(client._client.chat.completions, "create", side_effect=side_effect):
            resp = client.chat([{"role": "user", "content": "hi"}])
        assert resp.content == "hello"
        assert call_count == 3

    def test_raises_after_max_retries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("orc.ai.llm._RETRY_BASE_DELAY", 0.01)
        client = LLMClient(api_key="fake-key")
        exc = Exception("server error")
        exc.status_code = 500  # type: ignore[attr-defined]

        with patch.object(client._client.chat.completions, "create", side_effect=exc):
            with pytest.raises(Exception, match="server error"):
                client.chat([{"role": "user", "content": "hi"}])

    def test_non_retriable_raises_immediately(self) -> None:
        client = LLMClient(api_key="fake-key")
        exc = Exception("bad request")
        exc.status_code = 400  # type: ignore[attr-defined]

        with patch.object(client._client.chat.completions, "create", side_effect=exc):
            with pytest.raises(Exception, match="bad request"):
                client.chat([{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------------------
# ChatResponse / ToolCall dataclasses
# ---------------------------------------------------------------------------


class TestChatResponse:
    def test_has_tool_calls_true(self) -> None:
        resp = ChatResponse(
            content=None,
            tool_calls=[ToolCall(id="1", name="test", arguments="{}")],
            finish_reason="tool_calls",
        )
        assert resp.has_tool_calls is True

    def test_has_tool_calls_false(self) -> None:
        resp = ChatResponse(content="hi", tool_calls=[], finish_reason="stop")
        assert resp.has_tool_calls is False
