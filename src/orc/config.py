"""orc – path globals and environment validation."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import structlog
from dotenv import load_dotenv

logger = structlog.get_logger(__name__)

_PACKAGE_DIR = Path(__file__).parent
_PACKAGE_ROLES_DIR = _PACKAGE_DIR / "roles"
_TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "default"


def _find_config_dir(base: Path | None = None) -> Path | None:
    """Find the orc configuration directory.

    Resolution order:
    1. ``ORC_DIR`` environment variable (absolute path, used as-is).
    2. ``{base}/.orc/`` — new default name.
    3. ``{base}/orc/`` — legacy name (backward compatibility).

    *base* defaults to ``Path.cwd()`` when omitted.
    Returns the first existing directory, or ``None`` if none is found.
    """
    env = os.environ.get("ORC_DIR", "").strip()
    if env:
        return Path(env).resolve()
    search = (base or Path.cwd()).resolve()
    for name in (".orc", "orc"):
        candidate = search / name
        if candidate.is_dir():
            return candidate
    return None


def _init_paths(agents_dir: Path, repo_root: Path | None = None) -> None:
    """(Re)initialise all module-level path globals.

    *repo_root* is the project root (where ``README.md`` and git live).
    When omitted it falls back to ``agents_dir.parent``, which is correct
    for the common case where the config dir sits directly inside the project
    root (e.g. ``{project}/.orc/`` or ``{project}/orc/``).

    Pass ``repo_root=Path.cwd()`` explicitly when the config dir is nested
    deeper (e.g. ``{project}/src/.orc/`` with ``--config-dir src``).

    ``.env`` is always resolved relative to ``Path.cwd()`` so that orc reads
    the credentials of the project you are running it *from*, regardless of
    where the config dir lives.
    """
    global AGENTS_DIR, WORK_DIR, BOARD_FILE, ROLES_DIR, REPO_ROOT, ENV_FILE
    global DEV_WORKTREE, _worktree_sibling
    AGENTS_DIR = agents_dir
    REPO_ROOT = (repo_root or agents_dir.parent).resolve()
    WORK_DIR = agents_dir / "work"
    BOARD_FILE = WORK_DIR / "board.yaml"
    ROLES_DIR = agents_dir / "roles"
    ENV_FILE = Path.cwd() / ".env"
    load_dotenv(ENV_FILE)
    _worktree_sibling = REPO_ROOT.parent / f"{REPO_ROOT.name}-dev"
    DEV_WORKTREE = (
        _worktree_sibling
        if os.access(REPO_ROOT.parent, os.W_OK)
        else Path("/tmp") / f"{REPO_ROOT.name}-dev"
    )


# Initialise at import time (best-effort; commands re-validate at runtime).
_found_at_import = _find_config_dir()
_init_paths(_found_at_import if _found_at_import is not None else Path.cwd() / ".orc")

WORK_DEV_BRANCH = "dev"


def _load_placeholders() -> frozenset[str]:
    """Read unfilled placeholder values from .env.example."""
    values: set[str] = {""}
    env_example = _TEMPLATES_DIR / ".env.example"
    for line in env_example.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            val = line.split("=", 1)[1].strip()
            if val and ("your-" in val or val.endswith("-here")):
                values.add(val)
    return frozenset(values)


_PLACEHOLDERS = _load_placeholders()


def validate_env() -> list[str]:
    """Check that all required .env variables are present and not placeholders."""
    errors: list[str] = []

    if not ENV_FILE.exists():
        errors.append(
            f".env not found at {ENV_FILE}. Copy .env.example to .env and fill in your credentials."
        )
        return errors

    # Telegram is optional — no validation here; orc works without it.
    # (see src/orc/telegram.py for graceful-degradation behaviour)

    ai_cli = os.environ.get("COLONY_AI_CLI", "").strip().lower()
    if not ai_cli or ai_cli in _PLACEHOLDERS:
        errors.append("COLONY_AI_CLI is not set. Valid values: copilot, claude.")
    elif ai_cli not in {"copilot", "claude"}:
        errors.append(f"COLONY_AI_CLI={ai_cli!r} is not supported. Valid values: copilot, claude.")

    if ai_cli == "claude":
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key or key in _PLACEHOLDERS:
            errors.append(
                "ANTHROPIC_API_KEY is not set. "
                "For claude, set it to your Anthropic API key in .env."
            )
    else:
        gh_token = os.environ.get("GH_TOKEN", "").strip()
        if not gh_token or gh_token in _PLACEHOLDERS:
            apps_json = Path.home() / ".config" / "github-copilot" / "apps.json"
            has_apps_token = False
            if apps_json.exists():
                try:
                    data = json.loads(apps_json.read_text())
                    entry = next(iter(data.values()))
                    has_apps_token = bool(entry.get("oauth_token", ""))
                except Exception:
                    pass
            if not has_apps_token:
                try:
                    result = subprocess.run(
                        ["gh", "auth", "token"], capture_output=True, text=True, check=True
                    )
                    if not result.stdout.strip():
                        raise ValueError("empty")
                except Exception:
                    errors.append(
                        "No GitHub token found for the copilot backend. "
                        "Set GH_TOKEN in .env, or run 'copilot /login'."
                    )

    return errors
