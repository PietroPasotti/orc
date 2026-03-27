"""AI backend for the orc orchestrator — internal agentic loop.

The internal backend calls LLM APIs directly (via the ``openai`` SDK) and
executes tools in-process, replacing the previous CLI-based backends
(``copilot``, ``claude``) that shelled out to external binaries.

Architecture
------------
``InternalBackend.spawn()`` starts an :class:`~orc.ai.runner.AgentRunner`
in a background thread and returns a :class:`SpawnResult` whose ``process``
field is a :class:`ThreadProcessAdapter` — a duck-type of ``subprocess.Popen``
that the pool and dispatcher can track without any code changes.

Provider configuration
----------------------
The LLM provider (Gemini, GitHub Models, OpenAI) is configured via the squad
profile's ``provider`` field (default: ``"gemini"``).  See
:mod:`orc.ai.llm` for provider presets and authentication.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

import structlog

from orc.ai.llm import LLMClient
from orc.ai.runner import AgentRunner, RunnerConfig
from orc.ai.tools import ToolExecutor
from orc.engine.pool import ProcessLike
from orc.squad import AgentRole, PermissionConfig

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Spawn result
# ---------------------------------------------------------------------------


@dataclass
class SpawnResult:
    """Encapsulates the result of a non-blocking agent spawn.

    The ``process`` field satisfies the :class:`~orc.engine.pool.ProcessLike`
    protocol, which both ``subprocess.Popen`` and :class:`ThreadProcessAdapter`
    implement.
    """

    process: ProcessLike
    """The process handle (ThreadProcessAdapter for internal backend)."""

    log_fh: IO[str] | None
    """Open log file handle, or ``None`` when no *log_path* was given."""

    context_tmp: str | None = field(default=None)
    """Path to a temporary prompt file (unused by internal backend)."""

    mcp_config_tmp: str | None = field(default=None)
    """Path to a temporary MCP config file (unused by internal backend)."""


# ---------------------------------------------------------------------------
# Thread process adapter
# ---------------------------------------------------------------------------


class ThreadProcessAdapter:
    """Makes a ``threading.Thread`` running an agent look like ``subprocess.Popen``.

    Satisfies the :class:`~orc.engine.pool.ProcessLike` protocol so the
    :class:`~orc.engine.pool.AgentPool` can track thread-based agents
    alongside subprocess-based ones.
    """

    def __init__(self, thread: threading.Thread, cancel_event: threading.Event) -> None:
        self._thread = thread
        self._cancel_event = cancel_event
        self._exit_code: int | None = None

    def set_exit_code(self, code: int) -> None:
        """Set the exit code (called by the runner thread on completion)."""
        self._exit_code = code

    def poll(self) -> int | None:
        """Return the exit code if finished, or ``None`` if still running."""
        if self._thread.is_alive():
            return None
        return self._exit_code if self._exit_code is not None else 1

    def kill(self) -> None:
        """Signal the agent to cancel."""
        self._cancel_event.set()

    def wait(self, timeout: float | None = None) -> int:
        """Block until the agent thread finishes and return the exit code."""
        self._thread.join(timeout=timeout)
        return self._exit_code if self._exit_code is not None else 1


# ---------------------------------------------------------------------------
# Internal backend
# ---------------------------------------------------------------------------

# Default provider and model — can be overridden via squad config.
_DEFAULT_PROVIDER = "gemini"
_DEFAULT_MODEL = "gemini-2.5-pro"


class InternalBackend:
    """Agent backend that runs an internal agentic loop in a thread.

    No external CLI binaries are needed.  Tools are executed in-process
    and LLM calls go through the ``openai`` SDK.
    """

    def __init__(
        self,
        provider: str = _DEFAULT_PROVIDER,
        default_model: str = _DEFAULT_MODEL,
    ) -> None:
        self.provider = provider
        self.default_model = default_model

    @property
    def name(self) -> str:
        return "internal"

    def invoke(
        self,
        context: tuple[str, str],
        cwd: Path | None = None,
        model: str | None = None,
        agent_id: str | None = None,
        role: str | None = None,
        permissions: PermissionConfig | None = None,
    ) -> int:
        """Run an agent synchronously (blocking).

        Used for ad-hoc invocations like conflict resolution.
        """
        _permissions = permissions or PermissionConfig(mode="yolo")
        _model = model or self.default_model
        _cwd = cwd or Path.cwd()
        _role = AgentRole(role) if role else AgentRole.CODER

        client = LLMClient(provider=self.provider, model=_model)
        executor = ToolExecutor(
            cwd=_cwd,
            role=_role,
            permissions=_permissions,
            socket_path=os.environ.get("ORC_API_SOCKET", ""),
            agent_id=agent_id or "ad-hoc",
        )
        config = RunnerConfig()
        runner = AgentRunner(client, executor, config)

        system_prompt, user_prompt = context
        return runner.run(system_prompt, user_prompt)

    def spawn(
        self,
        context: tuple[str, str],
        cwd: Path,
        model: str | None = None,
        log_path: Path | None = None,
        agent_id: str | None = None,
        role: str | None = None,
        permissions: PermissionConfig | None = None,
    ) -> SpawnResult:
        """Spawn an agent in a background thread.

        Returns a :class:`SpawnResult` with a :class:`ThreadProcessAdapter`
        that the pool can track like a subprocess.
        """
        _permissions = permissions or PermissionConfig(mode="yolo")
        _model = model or self.default_model
        _role = AgentRole(role) if role else AgentRole.CODER

        client = LLMClient(provider=self.provider, model=_model)
        executor = ToolExecutor(
            cwd=cwd,
            role=_role,
            permissions=_permissions,
            socket_path=os.environ.get("ORC_API_SOCKET", ""),
            agent_id=agent_id or "unknown",
        )

        # Set up logging.
        log_fh: IO[str] | None = None
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_fh = open(log_path, "w", encoding="utf-8", buffering=1)  # noqa: SIM115

        cancel_event = threading.Event()
        config = RunnerConfig(log_fh=log_fh, cancel_event=cancel_event)
        runner = AgentRunner(client, executor, config)

        system_prompt, user_prompt = context

        adapter = ThreadProcessAdapter(
            thread=threading.Thread(target=lambda: None),  # placeholder
            cancel_event=cancel_event,
        )

        def _run_agent() -> None:
            try:
                exit_code = runner.run(system_prompt, user_prompt)
                adapter.set_exit_code(exit_code)
            except Exception as exc:
                logger.error("agent_thread_crash", agent_id=agent_id, error=str(exc))
                adapter.set_exit_code(1)

        thread = threading.Thread(target=_run_agent, name=f"agent-{agent_id}", daemon=True)
        adapter._thread = thread
        thread.start()

        return SpawnResult(
            process=adapter,
            log_fh=log_fh,
            context_tmp=None,
            mcp_config_tmp=None,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
