"""AI invocation layer — thin façade over :class:`~orc.ai.backends.InternalBackend`.

All agent invocations go through the internal backend, which runs an
agentic loop in-process using the ``openai`` SDK.  No external CLI
binaries are required.

Provider and model are configured via squad profiles (``provider`` and
``model`` fields).  For ad-hoc invocations (conflict resolution), defaults
to the Gemini provider.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from orc.ai.backends import InternalBackend, SpawnResult
from orc.squad import PermissionConfig

load_dotenv()  # auto-discovers .env from CWD upward

# Singleton backend instance.
_backend = InternalBackend()


def invoke(
    context: tuple[str, str],
    cwd: Path | None = None,
    model: str | None = None,
    agent_id: str | None = None,
    role: str | None = None,
    permissions: PermissionConfig | None = None,
) -> int:
    """Invoke an agent synchronously with *context* as the prompt.

    Returns the agent exit code (0 = success).
    """
    return _backend.invoke(
        context,
        cwd=cwd,
        model=model,
        agent_id=agent_id,
        role=role,
        permissions=permissions,
    )


def spawn(
    context: tuple[str, str],
    cwd: Path,
    model: str | None = None,
    log_path: Path | None = None,
    agent_id: str | None = None,
    role: str | None = None,
    permissions: PermissionConfig | None = None,
) -> SpawnResult:
    """Spawn an agent as a **non-blocking** background thread.

    Returns a :class:`~orc.ai.backends.SpawnResult` with the process handle,
    optional log file handle, and temp file paths for cleanup.
    """
    return _backend.spawn(
        context,
        cwd=cwd,
        model=model,
        log_path=log_path,
        agent_id=agent_id,
        role=role,
        permissions=permissions,
    )
