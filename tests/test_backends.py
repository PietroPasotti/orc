"""Tests for orc/backends.py — InternalBackend and ThreadProcessAdapter."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from orc.ai.backends import (
    InternalBackend,
    SpawnResult,
    ThreadProcessAdapter,
    _split_context,
)
from orc.ai.llm import ChatResponse
from orc.engine.pool import ProcessLike

# ---------------------------------------------------------------------------
# ThreadProcessAdapter
# ---------------------------------------------------------------------------


class TestThreadProcessAdapter:
    def test_poll_returns_none_while_running(self) -> None:
        event = threading.Event()
        thread = threading.Thread(target=lambda: event.wait())
        adapter = ThreadProcessAdapter(thread, threading.Event())
        thread.start()
        try:
            assert adapter.poll() is None
        finally:
            event.set()
            thread.join()

    def test_poll_returns_exit_code_after_finish(self) -> None:
        thread = threading.Thread(target=lambda: None)
        adapter = ThreadProcessAdapter(thread, threading.Event())
        adapter.set_exit_code(0)
        thread.start()
        thread.join()
        assert adapter.poll() == 0

    def test_poll_returns_1_when_no_exit_code_set(self) -> None:
        thread = threading.Thread(target=lambda: None)
        adapter = ThreadProcessAdapter(thread, threading.Event())
        thread.start()
        thread.join()
        assert adapter.poll() == 1  # default

    def test_kill_sets_cancel_event(self) -> None:
        cancel = threading.Event()
        thread = threading.Thread(target=lambda: None)
        adapter = ThreadProcessAdapter(thread, cancel)
        adapter.kill()
        assert cancel.is_set()

    def test_wait_returns_exit_code(self) -> None:
        def worker(adapter: ThreadProcessAdapter) -> None:
            time.sleep(0.05)
            adapter.set_exit_code(42)

        thread = threading.Thread(target=lambda: worker(adapter))
        adapter = ThreadProcessAdapter(thread, threading.Event())
        thread.start()
        result = adapter.wait(timeout=5)
        assert result == 42

    def test_satisfies_process_like_protocol(self) -> None:
        thread = threading.Thread(target=lambda: None)
        adapter = ThreadProcessAdapter(thread, threading.Event())
        assert isinstance(adapter, ProcessLike)


# ---------------------------------------------------------------------------
# SpawnResult
# ---------------------------------------------------------------------------


class TestSpawnResult:
    def test_context_tmp_defaults_to_none(self) -> None:
        thread = threading.Thread(target=lambda: None)
        adapter = ThreadProcessAdapter(thread, threading.Event())
        result = SpawnResult(process=adapter, log_fh=None)
        assert result.context_tmp is None
        assert result.mcp_config_tmp is None


# ---------------------------------------------------------------------------
# InternalBackend
# ---------------------------------------------------------------------------


class TestInternalBackend:
    def test_name(self) -> None:
        b = InternalBackend()
        assert b.name == "internal"

    def test_default_provider_is_gemini(self) -> None:
        b = InternalBackend()
        assert b.provider == "gemini"

    def test_custom_provider(self) -> None:
        b = InternalBackend(provider="openai", default_model="gpt-4o")
        assert b.provider == "openai"
        assert b.default_model == "gpt-4o"

    def test_spawn_returns_spawn_result(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("GEMINI_API_TOKEN", "fake-key")
        b = InternalBackend()
        # Mock LLMClient to avoid real API calls.
        text_resp = ChatResponse(content="done", tool_calls=[], finish_reason="stop", usage={})
        with patch("orc.ai.backends.LLMClient") as MockClient:
            MockClient.return_value.chat.return_value = text_resp
            result = b.spawn(
                "system prompt\n---\nuser prompt",
                cwd=tmp_path,
                model="test-model",
                agent_id="coder-1",
                role="coder",
            )
        assert isinstance(result, SpawnResult)
        assert isinstance(result.process, ThreadProcessAdapter)
        # Wait for the thread to finish.
        result.process.wait(timeout=5)
        assert result.process.poll() == 0

    def test_spawn_with_log_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("GEMINI_API_TOKEN", "fake-key")
        b = InternalBackend()
        log_path = tmp_path / "logs" / "agent.log"
        text_resp = ChatResponse(
            content="done",
            tool_calls=[],
            finish_reason="stop",
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )
        with patch("orc.ai.backends.LLMClient") as MockClient:
            MockClient.return_value.chat.return_value = text_resp
            result = b.spawn(
                "system\n---\nuser",
                cwd=tmp_path,
                log_path=log_path,
                agent_id="qa-1",
                role="qa",
            )
        result.process.wait(timeout=5)
        assert result.log_fh is not None
        result.log_fh.close()
        assert log_path.exists()
        content = log_path.read_text()
        assert "Agent started" in content

    def test_invoke_returns_exit_code(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("GEMINI_API_TOKEN", "fake-key")
        b = InternalBackend()
        text_resp = ChatResponse(content="done", tool_calls=[], finish_reason="stop", usage={})
        with patch("orc.ai.backends.LLMClient") as MockClient:
            MockClient.return_value.chat.return_value = text_resp
            rc = b.invoke("system\n---\nuser", cwd=tmp_path)
        assert rc == 0


# ---------------------------------------------------------------------------
# _split_context
# ---------------------------------------------------------------------------


class TestSplitContext:
    def test_splits_on_separator(self) -> None:
        system, user = _split_context("role instructions\n---\ntask description")
        assert system == "role instructions"
        assert user == "task description"

    def test_no_separator_uses_default_system(self) -> None:
        system, user = _split_context("just a plain prompt")
        assert "AI agent" in system
        assert user == "just a plain prompt"

    def test_multiple_separators_splits_on_first(self) -> None:
        system, user = _split_context("part1\n---\npart2\n---\npart3")
        assert system == "part1"
        assert user == "part2\n---\npart3"
