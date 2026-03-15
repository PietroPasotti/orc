"""AI CLI backend protocol and implementations for the orc orchestrator.

Backend selection
-----------------
:func:`get_backend` reads ``COLONY_AI_CLI`` from the environment and returns the
matching backend instance.

Supported backends
~~~~~~~~~~~~~~~~~~

``copilot``
    Calls ``copilot [permission flags] --additional-mcp-config @<mcp_file>
    --prompt @<context_file>``.
    In yolo mode: ``copilot --yolo --prompt @<context_file>``.
    Token resolution order:
    1. ``GH_TOKEN`` environment variable.
    2. ``~/.config/github-copilot/apps.json``.
    3. ``gh auth token``.

``claude``
    Calls ``claude -p @<context_file> [--model <model>] [--mcp-config <mcp_file>]
    [--allowedTools ...]``.
    In yolo mode: ``claude -p @<context_file> [--model <model>]
    --dangerouslySkipPermissions``.
    Requires ``ANTHROPIC_API_KEY``.

Adding a new backend
--------------------
1. Subclass :class:`BaseAIBackend`.
2. Register it in :data:`_BACKEND_REGISTRY`.
3. Users set ``COLONY_AI_CLI=<name>`` in their ``.env``.

MCP config generation
---------------------
:meth:`BaseAIBackend.generate_mcp_config` writes a temporary JSON file
(``{"mcpServers": {"orc": ...}}``) and returns its path.  The file is
tracked in :attr:`SpawnResult.mcp_config_tmp` and deleted alongside the
context temp file when the agent process exits.

Permission flag translation
---------------------------
Each backend translates the abstract :class:`~orc.squad.PermissionConfig`
from the squad profile into CLI-specific flags.  The abstract tool names used
in squad YAML map to CLI-specific patterns via :data:`_COPILOT_TOOL_MAP` and
:data:`_CLAUDE_TOOL_MAP`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from orc.squad import PermissionConfig

# ---------------------------------------------------------------------------
# Spawn result
# ---------------------------------------------------------------------------


@dataclass
class SpawnResult:
    """Encapsulates the result of a non-blocking AI CLI spawn.

    Replaces the previous convention of monkey-patching ``_context_tmp`` onto
    the :class:`subprocess.Popen` object.
    """

    process: subprocess.Popen[bytes]
    """The spawned subprocess handle."""

    log_fh: IO[str] | None
    """Open log file handle, or ``None`` when no *log_path* was given."""

    context_tmp: str
    """Path to the temporary prompt file; must be deleted after the process exits."""

    mcp_config_tmp: str | None = field(default=None)
    """Path to the temporary MCP config JSON file, or ``None`` when not used."""


# ---------------------------------------------------------------------------
# Permission → CLI flag translation maps
# ---------------------------------------------------------------------------

# Maps abstract tool names from squad YAML to Copilot CLI --allow-tool patterns.
# Keys are exact matches; a missing key is passed through unchanged (for custom
# patterns the user may add, e.g. "shell(npm:*)").
_COPILOT_TOOL_MAP: dict[str, str] = {
    "orc": "orc",
    "read": "read",
    "write": "write",
    "shell(git:*)": "shell(git:*)",
}

# Maps abstract tool names to Claude --allowedTools patterns.
_CLAUDE_TOOL_MAP: dict[str, str] = {
    "orc": "mcp__orc__*",
    "read": "Read",
    "write": "Write",
    "shell(git:*)": "Bash(git *)",
}


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
    def _build_command(
        self,
        prompt_file: str,
        model: str | None,
        mcp_config_file: str | None,
        permissions: PermissionConfig,
    ) -> list[str]:
        """Return the full CLI command list for *prompt_file*."""
        ...

    @abstractmethod
    def _prepare_env(self) -> dict[str, str]:
        """Return the environment dict (``os.environ.copy()`` plus credentials)."""
        ...

    def generate_mcp_config(
        self,
        agent_id: str,
        role: str,
        socket_path: str,
    ) -> str:
        """Write a temporary MCP config JSON and return its path.

        The config tells the CLI how to launch the orc MCP server as a stdio
        subprocess.  The file must be deleted by the caller after the agent exits.

        Parameters
        ----------
        agent_id:
            Agent identifier (e.g. ``"coder-1"``).
        role:
            Agent role (``"planner"``, ``"coder"``, or ``"qa"``).
        socket_path:
            Path to the orc coordination API Unix socket.
        """
        orc_package_dir = str(Path(__file__).parent.parent.parent)
        config = {
            "mcpServers": {
                "orc": {
                    "command": sys.executable,
                    "args": ["-m", "orc.mcp"],
                    "env": {
                        "ORC_API_SOCKET": socket_path,
                        "ORC_AGENT_ID": agent_id,
                        "ORC_AGENT_ROLE": role,
                        "PYTHONPATH": orc_package_dir,
                    },
                }
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            json.dump(config, tmp)
            return tmp.name

    def invoke(
        self,
        context: str,
        cwd: Path | None = None,
        model: str | None = None,
        agent_id: str | None = None,
        role: str | None = None,
        permissions: PermissionConfig | None = None,
    ) -> int:
        """Write *context* to a temp file and invoke the AI CLI synchronously.

        When *permissions* is ``None`` the backend defaults to yolo mode,
        preserving backward-compatible behaviour for ad-hoc invocations (e.g.
        conflict resolution) that run outside the squad-config path.
        """
        _permissions = permissions if permissions is not None else PermissionConfig(mode="yolo")
        mcp_config_file: str | None = None
        socket_path = os.environ.get("ORC_API_SOCKET", "")

        if agent_id and role and socket_path and not _permissions.is_yolo:
            mcp_config_file = self.generate_mcp_config(agent_id, role, socket_path)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
            tmp.write(context)
            tmp_path = tmp.name
        try:
            env = self._prepare_env()
            cmd = self._build_command(tmp_path, model, mcp_config_file, _permissions)
            result = subprocess.run(cmd, cwd=cwd, env=env)
            return result.returncode
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            if mcp_config_file:
                Path(mcp_config_file).unlink(missing_ok=True)

    def spawn(
        self,
        context: str,
        cwd: Path,
        model: str | None = None,
        log_path: Path | None = None,
        agent_id: str | None = None,
        role: str | None = None,
        permissions: PermissionConfig | None = None,
    ) -> SpawnResult:
        """Write *context* to a temp file and spawn the AI CLI non-blocking.

        When *permissions* is ``None`` the backend defaults to yolo mode.
        """
        _permissions = permissions if permissions is not None else PermissionConfig(mode="yolo")
        mcp_config_file: str | None = None
        socket_path = os.environ.get("ORC_API_SOCKET", "")

        if agent_id and role and socket_path and not _permissions.is_yolo:
            mcp_config_file = self.generate_mcp_config(agent_id, role, socket_path)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
            tmp.write(context)
            tmp_path = tmp.name

        env = self._prepare_env()
        cmd = self._build_command(tmp_path, model, mcp_config_file, _permissions)

        log_fh: IO[str] | None = None
        stdout: IO[str] | int
        stderr: IO[str] | int
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_fh = open(log_path, "w", encoding="utf-8", buffering=1)  # noqa: SIM115
            stdout = log_fh
            stderr = log_fh
        else:
            stdout = subprocess.DEVNULL
            stderr = subprocess.DEVNULL

        process = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=stdout, stderr=stderr)
        return SpawnResult(
            process=process,
            log_fh=log_fh,
            context_tmp=tmp_path,
            mcp_config_tmp=mcp_config_file,
        )


# ---------------------------------------------------------------------------
# Copilot backend
# ---------------------------------------------------------------------------


class CopilotBackend(BaseAIBackend):
    """GitHub Copilot CLI backend.

    In confined mode (default)::

        copilot --allow-tool=<t1> ... --deny-tool=<d1> ... \\
                --additional-mcp-config @<mcp_file> --prompt @<context_file>

    In yolo mode::

        copilot --yolo --prompt @<context_file>
    """

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
                t = str(entry.get("oauth_token", ""))
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

    def _permission_flags(self, permissions: PermissionConfig) -> list[str]:
        """Translate a :class:`PermissionConfig` into Copilot CLI flags."""
        if permissions.is_yolo:
            return ["--yolo"]
        flags: list[str] = []
        for tool in permissions.allow_tools:
            cli_tool = _COPILOT_TOOL_MAP.get(tool, tool)
            flags += [f"--allow-tool={cli_tool}"]
        for tool in permissions.deny_tools:
            cli_tool = _COPILOT_TOOL_MAP.get(tool, tool)
            flags += [f"--deny-tool={cli_tool}"]
        return flags

    def _build_command(
        self,
        prompt_file: str,
        model: str | None,
        mcp_config_file: str | None,
        permissions: PermissionConfig,
    ) -> list[str]:
        perm_flags = self._permission_flags(permissions)
        cmd = ["copilot"] + perm_flags
        if mcp_config_file and not permissions.is_yolo:
            cmd += ["--additional-mcp-config", f"@{mcp_config_file}"]
        cmd += ["--prompt", f"@{prompt_file}"]
        return cmd

    def _prepare_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["GH_TOKEN"] = self.resolve_token()
        return env


# ---------------------------------------------------------------------------
# Claude backend
# ---------------------------------------------------------------------------


class ClaudeBackend(BaseAIBackend):
    """Anthropic Claude CLI backend.

    In confined mode (default)::

        claude -p @<context_file> [--model <model>] \\
               --mcp-config <mcp_file> --allowedTools <t1> <t2> ...

    In yolo mode::

        claude -p @<context_file> [--model <model>] --dangerouslySkipPermissions
    """

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

    def _permission_flags(self, permissions: PermissionConfig) -> list[str]:
        """Translate a :class:`PermissionConfig` into Claude CLI flags."""
        if permissions.is_yolo:
            return ["--dangerouslySkipPermissions"]
        if not permissions.allow_tools:
            return []
        allowed = [_CLAUDE_TOOL_MAP.get(t, t) for t in permissions.allow_tools]
        return ["--allowedTools"] + allowed

    def _build_command(
        self,
        prompt_file: str,
        model: str | None,
        mcp_config_file: str | None,
        permissions: PermissionConfig,
    ) -> list[str]:
        cmd = ["claude", "-p", f"@{prompt_file}"]
        if model:
            cmd += ["--model", model]
        if mcp_config_file and not permissions.is_yolo:
            cmd += ["--mcp-config", mcp_config_file]
        cmd += self._permission_flags(permissions)
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
