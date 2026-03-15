"""AI CLI invocation layer — thin façade over :mod:`orc.ai.backends`.

Backend selection
-----------------
``COLONY_AI_CLI`` (default: ``copilot``) controls which CLI is used.
See :mod:`orc.ai.backends` for supported backends and their configuration.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from orc.ai.backends import SpawnResult, get_backend
from orc.squad import PermissionConfig

load_dotenv()  # auto-discovers .env from CWD upward

_CLI = os.environ.get("COLONY_AI_CLI", "copilot").strip().lower()


def _require_config() -> None:
    """Raise :class:`OSError` if ``_CLI`` names an unsupported backend."""
    get_backend(_CLI)


def invoke(
    context: str,
    cwd: Path | None = None,
    model: str | None = None,
    agent_id: str | None = None,
    role: str | None = None,
    permissions: PermissionConfig | None = None,
) -> int:
    """Invoke the configured AI CLI with *context* as the prompt.

    *model* is forwarded to the backend where supported (``claude`` only).
    *agent_id*, *role*, and *permissions* enable MCP config generation and
    permission-flag injection when set.

    Returns the subprocess exit code.
    Raises :class:`OSError` if ``COLONY_AI_CLI`` is invalid or a required
    credential is missing.
    """
    return get_backend(_CLI).invoke(
        context,
        cwd=cwd,
        model=model,
        agent_id=agent_id,
        role=role,
        permissions=permissions,
    )


def spawn(
    context: str,
    cwd: Path,
    model: str | None = None,
    log_path: Path | None = None,
    agent_id: str | None = None,
    role: str | None = None,
    permissions: PermissionConfig | None = None,
) -> SpawnResult:
    """Spawn the configured AI CLI as a **non-blocking** subprocess.

    Returns a :class:`~orc.ai.backends.SpawnResult` with the process handle,
    optional log file handle, and the temporary prompt file paths for cleanup.

    *agent_id*, *role*, and *permissions* enable MCP config generation and
    permission-flag injection when set.

    Raises :class:`OSError` if ``COLONY_AI_CLI`` is invalid or a required
    credential is missing.
    """
    return get_backend(_CLI).spawn(
        context,
        cwd=cwd,
        model=model,
        log_path=log_path,
        agent_id=agent_id,
        role=role,
        permissions=permissions,
    )
