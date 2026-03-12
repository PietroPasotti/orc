"""AI CLI invocation layer for the orc agent orchestrator.

Backend selection
-----------------
``COLONY_AI_CLI`` (default: ``copilot``) controls which CLI is used:

``copilot``
    Calls ``copilot --yolo --prompt <context>``.
    Token resolution order (first non-empty value wins):
      1. ``GH_TOKEN`` environment variable (set in ``.env`` or shell).
      2. ``~/.config/github-copilot/apps.json`` (written by ``copilot /login``).
      3. ``gh auth token`` (last resort).
    Raises ``EnvironmentError`` if no token is found via any of these methods.

``claude``
    Calls ``claude -p <context>`` (non-interactive print mode).
    Requires ``ANTHROPIC_API_KEY`` to be set in the environment or ``.env``.
    Raises ``EnvironmentError`` if the key is absent.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import IO

from dotenv import load_dotenv

load_dotenv()  # auto-discovers .env from CWD upward

_CLI = os.environ.get("COLONY_AI_CLI", "copilot").strip().lower()

_SUPPORTED = {"copilot", "claude"}
_COPILOT_APPS_JSON = Path.home() / ".config" / "github-copilot" / "apps.json"


def _require_config() -> None:
    if _CLI not in _SUPPORTED:
        raise OSError(
            f"COLONY_AI_CLI={_CLI!r} is not supported. "
            f"Valid values: {', '.join(sorted(_SUPPORTED))}."
        )


def _resolve_gh_token() -> str:
    """Return a GitHub token for the copilot backend.

    Resolution order:
    1. ``GH_TOKEN`` environment variable.
    2. ``~/.config/github-copilot/apps.json`` (written by ``copilot /login``).
    3. ``gh auth token``.

    Raises ``EnvironmentError`` if no token is found via any of these methods.
    """
    token = os.environ.get("GH_TOKEN", "").strip()
    if token:
        return token

    if _COPILOT_APPS_JSON.exists():
        try:
            data = json.loads(_COPILOT_APPS_JSON.read_text())
            entry = next(iter(data.values()))
            t = entry.get("oauth_token", "")
            if t:
                return t
        except Exception:
            pass

    try:
        result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True)
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


def _resolve_anthropic_key() -> str:
    """Return the Anthropic API key for the claude backend.

    Raises ``EnvironmentError`` if ``ANTHROPIC_API_KEY`` is not set.
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise OSError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file:\n"
            "  ANTHROPIC_API_KEY=sk-ant-..."
        )
    return key


def invoke(context: str, cwd: Path | None = None, model: str | None = None) -> int:
    """Invoke the configured AI CLI with *context* as the prompt.

    *model* is passed to the backend where supported:
    - ``claude``: forwarded as ``--model <model>``.
    - ``copilot``: ignored (the copilot CLI does not support model selection).

    Returns the subprocess exit code.
    Raises ``EnvironmentError`` if ``COLONY_AI_CLI`` is invalid or a required
    credential is missing.
    """
    _require_config()

    env = os.environ.copy()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
        tmp.write(context)
        tmp_path = tmp.name

    try:
        if _CLI == "copilot":
            env["GH_TOKEN"] = _resolve_gh_token()
            cmd = ["copilot", "--yolo", "--prompt", f"@{tmp_path}"]
        else:  # claude
            env["ANTHROPIC_API_KEY"] = _resolve_anthropic_key()
            cmd = ["claude", "-p", f"@{tmp_path}"]
            if model:
                cmd += ["--model", model]

        result = subprocess.run(cmd, cwd=cwd, env=env)
        return result.returncode
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def spawn(
    context: str,
    cwd: Path,
    model: str | None = None,
    log_path: Path | None = None,
) -> tuple[subprocess.Popen, IO[str] | None]:
    """Spawn the configured AI CLI as a **non-blocking** subprocess.

    Unlike :func:`invoke`, this returns immediately with a ``(Popen, log_fh)``
    tuple.  The caller is responsible for polling the process and closing the
    log file handle when the process exits.

    *log_path* — if provided, the subprocess's stdout and stderr are both
    redirected to that file (line-buffered).  The open file handle is returned
    as the second element of the tuple so the caller can close it.  When
    *log_path* is ``None`` both streams are discarded.

    A temporary file containing *context* is created before the subprocess is
    started.  Its path is stored on the ``Popen`` object as
    ``process._context_tmp`` for cleanup; call
    ``Path(process._context_tmp).unlink(missing_ok=True)`` after the process
    exits.

    Raises ``EnvironmentError`` if ``COLONY_AI_CLI`` is invalid or a required
    credential is missing.
    """
    _require_config()

    env = os.environ.copy()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
        tmp.write(context)
        tmp_path = tmp.name

    if _CLI == "copilot":
        env["GH_TOKEN"] = _resolve_gh_token()
        cmd = ["copilot", "--yolo", "--prompt", f"@{tmp_path}"]
    else:  # claude
        env["ANTHROPIC_API_KEY"] = _resolve_anthropic_key()
        cmd = ["claude", "-p", f"@{tmp_path}"]
        if model:
            cmd += ["--model", model]

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
