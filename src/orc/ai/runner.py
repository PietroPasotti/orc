"""Internal agentic loop — replaces CLI conversation management.

The :class:`AgentRunner` implements the core LLM agent loop:

1. Build initial messages (system + user prompt).
2. Call LLM with messages and tool definitions.
3. If the response contains tool calls → execute them → append results → goto 2.
4. If the response is text-only → agent is done.
5. Stop if ``max_iterations`` is reached or cancellation is requested.

Logging
-------
Every LLM response and tool call/result is written to the agent's log file,
providing the same debugging view as CLI-based agent logs.

Cancellation
------------
The runner checks a :class:`threading.Event` between iterations. The dispatcher
or pool can set this event to cancel a running agent (e.g. on watchdog timeout).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import IO, Any

import structlog

from orc.ai.llm import ChatResponse, LLMClient
from orc.ai.tools import ToolExecutor

logger = structlog.get_logger(__name__)

_DEFAULT_MAX_ITERATIONS = 200


@dataclass
class RunnerConfig:
    """Configuration for an :class:`AgentRunner`."""

    max_iterations: int = _DEFAULT_MAX_ITERATIONS
    """Maximum number of LLM call iterations before the agent is stopped."""

    log_fh: IO[str] | None = None
    """Open file handle for the agent's log file (optional)."""

    cancel_event: threading.Event = field(default_factory=threading.Event)
    """Event to signal cancellation from outside the runner."""


class AgentRunner:
    """Runs an agentic loop: LLM → tools → LLM → ... until done.

    Parameters
    ----------
    client:
        The LLM client to use for chat completions.
    tools:
        The tool executor providing tool definitions and execution.
    config:
        Runner configuration (max iterations, logging, cancellation).
    """

    def __init__(
        self,
        client: LLMClient,
        tools: ToolExecutor,
        config: RunnerConfig | None = None,
    ) -> None:
        self.client = client
        self.tools = tools
        self.config = config or RunnerConfig()
        self._messages: list[dict[str, Any]] = []

    def run(self, system_prompt: str, user_prompt: str) -> int:
        """Run the agent loop.

        Returns ``0`` on success (agent completed normally), ``1`` if
        max iterations were reached, ``2`` if cancelled.
        """
        self._messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        self._log(f"=== Agent started (max_iterations={self.config.max_iterations}) ===\n")

        for iteration in range(1, self.config.max_iterations + 1):
            if self.config.cancel_event.is_set():
                self._log(f"\n=== Cancelled at iteration {iteration} ===\n")
                return 2

            try:
                response = self.client.chat(
                    self._messages,
                    tools=self.tools.definitions,
                )
            except Exception as exc:
                self._log(f"\n=== LLM error at iteration {iteration}: {exc} ===\n")
                # Try context windowing: if it's a context-too-long error, trim messages.
                if self._try_context_recovery(exc):
                    continue
                return 1

            self._log_response(iteration, response)

            if response.has_tool_calls:
                # Append assistant message with tool calls.
                self._messages.append(self._build_assistant_message(response))
                # Execute each tool and append results.
                for tc in response.tool_calls:
                    result = self.tools.execute(tc)
                    self._log_tool_result(tc.name, tc.arguments, result)
                    self._messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        }
                    )
            elif response.finish_reason == "stop":
                # Agent is done — text-only response with explicit stop.
                self._log(f"\n=== Agent completed successfully after {iteration} iterations ===\n")
                if response.usage:
                    self._log(
                        f"Total tokens this call: {response.usage.get('total_tokens', '?')}\n"
                    )
                return 0
            else:
                # Non-stop finish without tool calls (e.g. Gemini's
                # MALFORMED_FUNCTION_CALL, length, content_filter).
                # Ask the model to try again.
                self._log(
                    f"\n--- Unexpected finish_reason={response.finish_reason!r} "
                    f"at iteration {iteration}, retrying ---\n"
                )
                self._messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Your last response had finish_reason={response.finish_reason!r} "
                            "and no tool calls. Please try again with a valid tool call "
                            "or respond with text to finish."
                        ),
                    }
                )

        self._log(f"\n=== Max iterations ({self.config.max_iterations}) reached ===\n")
        return 1

    # ------------------------------------------------------------------
    # Logging helpers
    # ------------------------------------------------------------------

    def _log(self, text: str) -> None:
        """Write to the log file handle if configured."""
        if self.config.log_fh is not None:
            try:
                self.config.log_fh.write(text)
                self.config.log_fh.flush()
            except Exception:
                pass

    def _log_response(self, iteration: int, response: ChatResponse) -> None:
        """Log an LLM response."""
        self._log(f"\n--- Iteration {iteration} (finish_reason={response.finish_reason}) ---\n")
        if response.content:
            self._log(f"Content: {response.content[:500]}\n")
        if response.has_tool_calls:
            for tc in response.tool_calls:
                self._log(f"Tool call: {tc.name}({tc.arguments})\n")
        if response.usage:
            tokens = response.usage.get("total_tokens", "?")
            self._log(f"Tokens: {tokens}\n")

    def _log_tool_result(self, name: str, arguments: str, result: str) -> None:
        """Log a tool execution result."""
        truncated = result[:1000] + "..." if len(result) > 1000 else result
        self._log(f"  → {name}: {truncated}\n")

    # ------------------------------------------------------------------
    # Context management
    # ------------------------------------------------------------------

    @staticmethod
    def _build_assistant_message(response: ChatResponse) -> dict[str, Any]:
        """Build a messages-format dict from an LLM response with tool calls."""
        msg: dict[str, Any] = {"role": "assistant"}
        if response.content:
            msg["content"] = response.content
        if response.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.arguments,
                    },
                }
                for tc in response.tool_calls
            ]
        return msg

    def _try_context_recovery(self, exc: Exception) -> bool:
        """Attempt to recover from a context-too-long error by trimming messages.

        Returns ``True`` if recovery was attempted (caller should retry),
        ``False`` if the error is not context-related.
        """
        err_str = str(exc).lower()
        context_errors = ("context_length_exceeded", "max_tokens", "too many tokens", "too long")
        if not any(marker in err_str for marker in context_errors):
            return False

        # Keep system prompt (index 0), last user message, and recent messages.
        # Drop the oldest non-system messages.
        if len(self._messages) <= 3:
            return False

        dropped = len(self._messages) // 3
        self._log(f"\n=== Context too long, dropping {dropped} oldest messages ===\n")
        # Keep system + trim oldest conversation turns.
        self._messages = [self._messages[0]] + self._messages[1 + dropped :]
        return True
