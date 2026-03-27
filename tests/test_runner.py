"""Tests for :mod:`orc.ai.runner` — agentic loop."""

from __future__ import annotations

import io
import threading
from pathlib import Path
from typing import Any

import pytest

from orc.ai.llm import ChatResponse, ToolCall
from orc.ai.runner import AgentRunner, RunnerConfig
from orc.ai.tools import ToolExecutor
from orc.squad import AgentRole, PermissionConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeLLMClient:
    """A mock LLMClient that returns scripted responses."""

    def __init__(self, responses: list[ChatResponse]) -> None:
        self._responses = list(responses)
        self._call_count = 0
        self.model = "test-model"
        self.provider = "test"

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        if self._call_count >= len(self._responses):
            raise RuntimeError("FakeLLMClient: no more scripted responses")
        resp = self._responses[self._call_count]
        self._call_count += 1
        return resp

    @property
    def call_count(self) -> int:
        return self._call_count


def _text_response(content: str = "Done!") -> ChatResponse:
    """A text-only response (agent is done)."""
    return ChatResponse(
        content=content,
        tool_calls=[],
        finish_reason="stop",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )


def _tool_response(calls: list[tuple[str, str, str]]) -> ChatResponse:
    """A response with tool calls. Each tuple is (id, name, arguments_json)."""
    return ChatResponse(
        content=None,
        tool_calls=[ToolCall(id=c[0], name=c[1], arguments=c[2]) for c in calls],
        finish_reason="tool_calls",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )


# ---------------------------------------------------------------------------
# Basic agent loop
# ---------------------------------------------------------------------------


class TestAgentRunnerBasic:
    @pytest.fixture()
    def executor(self, tmp_path: Path) -> ToolExecutor:
        return ToolExecutor(
            cwd=tmp_path,
            role=AgentRole.CODER,
            permissions=PermissionConfig(mode="yolo"),
        )

    def test_immediate_text_response(self, executor: ToolExecutor) -> None:
        client = FakeLLMClient([_text_response("All done")])
        runner = AgentRunner(client, executor)
        exit_code = runner.run("You are an agent.", "Do nothing.")
        assert exit_code == 0
        assert client.call_count == 1

    def test_tool_call_then_done(self, executor: ToolExecutor, tmp_path: Path) -> None:
        (tmp_path / "test.txt").write_text("hello world")
        client = FakeLLMClient(
            [
                _tool_response([("c1", "read_file", '{"path": "test.txt"}')]),
                _text_response("I read the file."),
            ]
        )
        runner = AgentRunner(client, executor)
        exit_code = runner.run("You are an agent.", "Read test.txt.")
        assert exit_code == 0
        assert client.call_count == 2

    def test_multiple_tool_calls(self, executor: ToolExecutor, tmp_path: Path) -> None:
        client = FakeLLMClient(
            [
                _tool_response(
                    [
                        ("c1", "write_file", '{"path": "a.txt", "content": "aaa"}'),
                        ("c2", "write_file", '{"path": "b.txt", "content": "bbb"}'),
                    ]
                ),
                _text_response("Created both files."),
            ]
        )
        runner = AgentRunner(client, executor)
        exit_code = runner.run("You are an agent.", "Create two files.")
        assert exit_code == 0
        assert (tmp_path / "a.txt").read_text() == "aaa"
        assert (tmp_path / "b.txt").read_text() == "bbb"

    def test_max_iterations(self, executor: ToolExecutor) -> None:
        # Agent always asks for tools, never completes.
        responses = [_tool_response([("c1", "list_directory", '{"path": "."}')]) for _ in range(5)]
        client = FakeLLMClient(responses)
        config = RunnerConfig(max_iterations=3)
        runner = AgentRunner(client, executor, config)
        exit_code = runner.run("You are an agent.", "Loop forever.")
        assert exit_code == 1  # max iterations reached
        assert client.call_count == 3


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


class TestAgentRunnerCancellation:
    def test_cancel_before_first_iteration(self, tmp_path: Path) -> None:
        executor = ToolExecutor(
            cwd=tmp_path,
            role=AgentRole.CODER,
            permissions=PermissionConfig(mode="yolo"),
        )
        cancel = threading.Event()
        cancel.set()  # Already cancelled
        config = RunnerConfig(cancel_event=cancel)
        client = FakeLLMClient([_text_response()])
        runner = AgentRunner(client, executor, config)
        exit_code = runner.run("system", "user")
        assert exit_code == 2
        assert client.call_count == 0

    def test_cancel_mid_loop(self, tmp_path: Path) -> None:
        executor = ToolExecutor(
            cwd=tmp_path,
            role=AgentRole.CODER,
            permissions=PermissionConfig(mode="yolo"),
        )
        cancel = threading.Event()

        call_count = 0
        original_responses = [
            _tool_response([("c1", "list_directory", '{"path": "."}')]),
            _tool_response([("c2", "list_directory", '{"path": "."}')]),
            _text_response("done"),
        ]

        class CancellingClient:
            model = "test"
            provider = "test"

            def chat(self, messages: Any, tools: Any = None, **kw: Any) -> ChatResponse:
                nonlocal call_count
                resp = original_responses[call_count]
                call_count += 1
                if call_count >= 2:
                    cancel.set()
                return resp

        config = RunnerConfig(cancel_event=cancel, max_iterations=10)
        runner = AgentRunner(CancellingClient(), executor, config)  # type: ignore[arg-type]
        exit_code = runner.run("system", "user")
        assert exit_code == 2
        assert call_count == 2


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class TestAgentRunnerLogging:
    def test_logs_to_file_handle(self, tmp_path: Path) -> None:
        executor = ToolExecutor(
            cwd=tmp_path,
            role=AgentRole.CODER,
            permissions=PermissionConfig(mode="yolo"),
        )
        log_buf = io.StringIO()
        config = RunnerConfig(log_fh=log_buf)
        client = FakeLLMClient(
            [
                _tool_response([("c1", "list_directory", '{"path": "."}')]),
                _text_response("Done"),
            ]
        )
        runner = AgentRunner(client, executor, config)
        runner.run("system", "user")

        log_content = log_buf.getvalue()
        assert "Agent started" in log_content
        assert "Iteration 1" in log_content
        assert "list_directory" in log_content
        assert "completed successfully" in log_content


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestAgentRunnerErrors:
    def test_tool_error_is_sent_to_llm(self, tmp_path: Path) -> None:
        executor = ToolExecutor(
            cwd=tmp_path,
            role=AgentRole.CODER,
            permissions=PermissionConfig(mode="yolo"),
        )
        client = FakeLLMClient(
            [
                _tool_response([("c1", "read_file", '{"path": "nonexistent.txt"}')]),
                _text_response("File not found, that's okay."),
            ]
        )
        runner = AgentRunner(client, executor)
        exit_code = runner.run("system", "user")
        assert exit_code == 0
        # Verify the error was sent back as a tool result.
        tool_msg = runner._messages[3]  # system, user, assistant, tool_result
        assert tool_msg["role"] == "tool"
        assert "Error" in tool_msg["content"]

    def test_llm_error_returns_1(self, tmp_path: Path) -> None:
        executor = ToolExecutor(
            cwd=tmp_path,
            role=AgentRole.CODER,
            permissions=PermissionConfig(mode="yolo"),
        )

        class FailingClient:
            model = "test"
            provider = "test"

            def chat(self, messages: Any, tools: Any = None, **kw: Any) -> ChatResponse:
                raise ConnectionError("network error")

        runner = AgentRunner(FailingClient(), executor)  # type: ignore[arg-type]
        exit_code = runner.run("system", "user")
        assert exit_code == 1


# ---------------------------------------------------------------------------
# Context recovery
# ---------------------------------------------------------------------------


class TestContextRecovery:
    def test_context_too_long_trims_messages(self, tmp_path: Path) -> None:
        executor = ToolExecutor(
            cwd=tmp_path,
            role=AgentRole.CODER,
            permissions=PermissionConfig(mode="yolo"),
        )

        call_count = 0

        class RecoveringClient:
            model = "test"
            provider = "test"

            def chat(self, messages: Any, tools: Any = None, **kw: Any) -> ChatResponse:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return _tool_response([("c1", "list_directory", '{"path": "."}')])
                if call_count == 2:
                    return _tool_response([("c2", "list_directory", '{"path": "."}')])
                if call_count == 3:
                    raise Exception("context_length_exceeded: too many tokens")
                if call_count == 4:
                    return _text_response("recovered!")
                raise RuntimeError("unexpected call")

        config = RunnerConfig(max_iterations=10)
        runner = AgentRunner(RecoveringClient(), executor, config)  # type: ignore[arg-type]
        exit_code = runner.run("system", "user")
        assert exit_code == 0
        assert call_count == 4

    def test_malformed_function_call_retries(self, tmp_path: Path) -> None:
        """Gemini's MALFORMED_FUNCTION_CALL should trigger a retry, not exit."""
        executor = ToolExecutor(
            cwd=tmp_path,
            role=AgentRole.CODER,
            permissions=PermissionConfig(mode="yolo"),
        )

        def _malformed_response() -> ChatResponse:
            return ChatResponse(
                content=None,
                tool_calls=[],
                finish_reason="function_call_filter: MALFORMED_FUNCTION_CALL",
                usage={"total_tokens": 100},
            )

        client = FakeLLMClient(
            [
                _malformed_response(),
                _text_response("OK, done for real."),
            ]
        )
        runner = AgentRunner(client, executor)
        exit_code = runner.run("system", "user")
        assert exit_code == 0
        assert client.call_count == 2
        # Verify a retry prompt was injected.
        user_msgs = [m for m in runner._messages if m["role"] == "user"]
        assert any("finish_reason" in m["content"] for m in user_msgs)

    def test_content_filter_retries(self, tmp_path: Path) -> None:
        """Non-stop finish reasons (e.g. content_filter) should retry."""
        executor = ToolExecutor(
            cwd=tmp_path,
            role=AgentRole.CODER,
            permissions=PermissionConfig(mode="yolo"),
        )
        client = FakeLLMClient(
            [
                ChatResponse(
                    content=None,
                    tool_calls=[],
                    finish_reason="content_filter",
                    usage={},
                ),
                _text_response("Retry worked."),
            ]
        )
        runner = AgentRunner(client, executor)
        exit_code = runner.run("system", "user")
        assert exit_code == 0
        assert client.call_count == 2
