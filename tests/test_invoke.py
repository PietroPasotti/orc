"""Tests for orc/invoke.py – internal backend dispatch."""

from pathlib import Path
from unittest.mock import patch

import pytest

from orc.ai import invoke as iv
from orc.ai.backends import SpawnResult, ThreadProcessAdapter
from orc.ai.llm import ChatResponse

# ---------------------------------------------------------------------------
# invoke()
# ---------------------------------------------------------------------------


class TestInvoke:
    def test_returns_zero_on_success(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("GEMINI_API_TOKEN", "fake-key")
        text_resp = ChatResponse(content="done", tool_calls=[], finish_reason="stop", usage={})
        with patch("orc.ai.backends.LLMClient") as MockClient:
            MockClient.return_value.chat.return_value = text_resp
            code = iv.invoke(("system", "user"), cwd=tmp_path)
        assert code == 0


# ---------------------------------------------------------------------------
# spawn()
# ---------------------------------------------------------------------------


class TestInvokeSpawn:
    def test_spawn_returns_spawn_result(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("GEMINI_API_TOKEN", "fake-key")
        text_resp = ChatResponse(content="done", tool_calls=[], finish_reason="stop", usage={})
        with patch("orc.ai.backends.LLMClient") as MockClient:
            MockClient.return_value.chat.return_value = text_resp
            result = iv.spawn(("system", "user"), cwd=tmp_path, model="test")
        assert isinstance(result, SpawnResult)
        assert isinstance(result.process, ThreadProcessAdapter)
        result.process.wait(timeout=5)
        assert result.process.poll() == 0

    def test_spawn_with_log_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("GEMINI_API_TOKEN", "fake-key")
        log_path = tmp_path / "agent.log"
        text_resp = ChatResponse(content="done", tool_calls=[], finish_reason="stop", usage={})
        with patch("orc.ai.backends.LLMClient") as MockClient:
            MockClient.return_value.chat.return_value = text_resp
            result = iv.spawn(("system", "user"), cwd=tmp_path, log_path=log_path)
        result.process.wait(timeout=5)
        assert result.log_fh is not None
        result.log_fh.close()
        assert log_path.exists()
