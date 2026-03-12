"""AI CLI backend protocol and implementations for the orc orchestrator.

Backend selection
-----------------
:func:`get_backend` reads ``COLONY_AI_CLI`` from the environment and returns the
matching backend instance.

Supported backends
~~~~~~~~~~~~~~~~~~

``copilot``
    Calls ``copilot --yolo --prompt @<context_file>``.
    Token resolution order:
    1. ``GH_TOKEN`` environment variable.
    2. ``~/.config/github-copilot/apps.json``.
    3. ``gh auth token``.

``claude``
    Calls ``claude -p @<context_file> [--model <model>]``.
    Requires ``ANTHROPIC_API_KEY``.

Adding a new backend
--------------------
1. Subclass :class:`BaseAIBackend` (or implement :class:`AIBackend` directly).
2. Register it in :data:`_BACKEND_REGISTRY`.
3. Users set ``COLONY_AI_CLI=<name>`` in their ``.env``.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import IO, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Protocol (structural typing contract)
# ---------------------------------------------------------------------------


@runtime_checkable
class AIBackend(Protocol):
    """Structural protocol for AI CLI backend implementations.

    Any object that implements :meth:`invoke` and :meth:`spawn` satisfies this
    protocol, whether or not it inherits from :class:`BaseAIBackend`.
    """

    @property
    def name(self) -> str:
        """Short identifier, e.g. ``'copilot'`` or ``'claude'``."""
        ...

    def invoke(self, context: str, cwd: Path | None = None, model: str | None = None) -> int:
        """Invoke the AI CLI synchronously. Returns the subprocess exit code."""
        ...

    def spawn(
        self,
        context: str,
        cwd: Path,
        model: str | None = None,
        log_path: Path | None = None,
    ) -> tuple[subprocess.Popen, IO[str] | None]:
        """Spawn the AI CLI as a **non-blocking** subprocess.

        Returns ``(process, log_fh)`` where *log_fh* is ``None`` when
        *log_path* is ``None``.
        """
        ...


# ---------------------------------------------------------------------------
# Base class with shared spawn/invoke helpers
# ---------------------------------------------------------------------------


class BaseAIBackend(ABC):
    """Abstract base providing shared :meth:`invoke` / :meth:`spawn` logic.

    Concrete subclasses only need to implement :meth:`_build_command` and
    :meth:`_prepare_env`.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def _build_command(self, prompt_file: str, model: str | None) -> list[str]:
        """Return the full CLI command list for *prompt_file*."""
        ...

    @abstractmethod
    def _prepare_env(self) -> dict[str, str]:
        """Return the environment dict (``os.environ.copy()`` plus credentials)."""
        ...

    def invoke(self, context: str, cwd: Path | None = None, model: str | None = None) -> int:
        """Write *context* to a temp file and invoke the AI CLI synchronously."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
            tmp.write(context)
            tmp_path = tmp.name
        try:
            env = self._prepare_env()
            cmd = self._build_command(tmp_path, model)
            result = subprocess.run(cmd, cwd=cwd, env=env)
            return result.returncode
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def spawn(
        self,
        context: str,
        cwd: Path,
        model: str | None = None,
        log_path: Path | None = None,
    ) -> tuple[subprocess.Popen, IO[str] | None]:
        """Write *context* to a temp file and spawn the AI CLI non-blocking."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
            tmp.write(context)
            tmp_path = tmp.name

        env = self._prepare_env()
        cmd = self._build_command(tmp_path, model)

        log_fh: IO[str] | None = None
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_fh = open(log_path, "w", encoding="utf-8", buffering=1)  # noqa: SIM115
            stdout = log_fh
            stderr = log_fh
        else:
            stdout = subprocess.DEVNULL
            stderr = subprocess.DEVNULL

        process = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=stdout, stderr=stderr)
        process._context_tmp = tmp_path  # type: ignore[attr-defined]
        return process, log_fh


# ---------------------------------------------------------------------------
# Copilot backend
# ---------------------------------------------------------------------------


class CopilotBackend(BaseAIBackend):
    """GitHub Copilot CLI backend (``copilot --yolo --prompt @<file>``)."""

    APPS_JSON: Path = Path.home() / ".config" / "github-copilot" / "apps.json"

    @property
    def name(self) -> str:
        return "copilot"

    def resolve_token(self) -> str:
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

        if self.APPS_JSON.exists():
            try:
                data = json.loads(self.APPS_JSON.read_text())
                entry = next(iter(data.values()))
                t = entry.get("oauth_token", "")
                if t:
                    return t
            except Exception:
                pass

        try:
            result = subprocess.run(
                ["gh", "auth", "token"], capture_output=True, text=True, check=True
            )
            t = result.stdout.strip()
            if t:
                return t
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        raise OSError(
            "No GitHub token found for the copilot backend. Either:\n"
            "  • Set GH_TOKEN in your .env file, or\n"
            "  • Run `copilot /login` to authenticate interactively."
        )

    def _build_command(self, prompt_file: str, model: str | None) -> list[str]:
        return ["copilot", "--yolo", "--prompt", f"@{prompt_file}"]

    def _prepare_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["GH_TOKEN"] = self.resolve_token()
        return env


# ---------------------------------------------------------------------------
# Claude backend
# ---------------------------------------------------------------------------


class ClaudeBackend(BaseAIBackend):
    """Anthropic Claude CLI backend (``claude -p @<file> [--model <model>]``)."""

    @property
    def name(self) -> str:
        return "claude"

    def resolve_key(self) -> str:
        """Return the Anthropic API key.

        Raises :class:`OSError` if ``ANTHROPIC_API_KEY`` is not set.
        """
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise OSError(
                "ANTHROPIC_API_KEY is not set. Add it to your .env file:\n"
                "  ANTHROPIC_API_KEY=sk-ant-..."
            )
        return key

    def _build_command(self, prompt_file: str, model: str | None) -> list[str]:
        cmd = ["claude", "-p", f"@{prompt_file}"]
        if model:
            cmd += ["--model", model]
        return cmd

    def _prepare_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["ANTHROPIC_API_KEY"] = self.resolve_key()
        return env


# ---------------------------------------------------------------------------
# Registry and factory
# ---------------------------------------------------------------------------

_BACKEND_REGISTRY: dict[str, type[BaseAIBackend]] = {
    "copilot": CopilotBackend,
    "claude": ClaudeBackend,
}

SUPPORTED_BACKENDS: frozenset[str] = frozenset(_BACKEND_REGISTRY)


def get_backend(name: str) -> BaseAIBackend:
    """Return an AI backend instance for *name*.

    Parameters
    ----------
    name:
        Backend identifier (e.g. ``"copilot"`` or ``"claude"``).

    Raises
    ------
    OSError
        If *name* is not a supported backend.
    """
    cls = _BACKEND_REGISTRY.get(name)
    if cls is None:
        raise OSError(
            f"COLONY_AI_CLI={name!r} is not supported. "
            f"Valid values: {', '.join(sorted(SUPPORTED_BACKENDS))}."
        )
    return cls()
