"""orc – configuration, path resolution, and environment validation.

No side effects at import time.  Call :func:`init` once during CLI
bootstrap, then use :func:`get` everywhere else.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import structlog
import yaml

logger = structlog.get_logger(__name__)

# ── Package-relative constants (truly static, safe at import time) ─────────
_PACKAGE_DIR = Path(__file__).parent
_PACKAGE_ROLES_DIR = _PACKAGE_DIR / "roles"
_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_ORC_CFG_TEMPLATE = _TEMPLATES_DIR / "default" / "orc_cfg"
_WORK_STATE_TEMPLATE = _TEMPLATES_DIR / "work_state"


def _xdg_cache_home() -> Path:
    """Return XDG_CACHE_HOME, defaulting to ``~/.cache``."""
    env = os.environ.get("XDG_CACHE_HOME", "").strip()
    return Path(env).expanduser().resolve() if env else Path.home() / ".cache"


def _orc_cache_root() -> Path:
    """Return the root directory for all orc project caches."""
    return _xdg_cache_home() / "orc" / "projects"


# ── Immutable config object ───────────────────────────────────────────────
@dataclass(frozen=True)
class Config:
    """All resolved paths and settings for the current orc session."""

    orc_dir: Path
    repo_root: Path
    work_dir: Path
    board_file: Path
    vision_dir: Path
    """Directory containing vision documents.  Points into the project cache
    when *project_id* is set; falls back to ``orc_dir/vision`` otherwise."""
    roles_dir: Path
    env_file: Path
    dev_worktree: Path
    worktree_base: Path
    work_dev_branch: str
    branch_prefix: str
    log_dir: Path
    todo_scan_exclude: tuple[str, ...]
    """Path patterns excluded from ``#TODO`` / ``#FIXME`` scans (git pathspec format)."""
    project_id: str = ""
    """Stable UUID stored in ``.orc/config.yaml`` under ``project-id``.
    Empty string when not yet set (board falls back to ``orc_dir/work/``)."""
    cache_dir: Path = Path()
    """Per-project cache root.  Resolution order:

    1. ``orc-cache-dir`` in ``config.yaml`` (explicit override, any path).
    2. ``~/.cache/orc/projects/{project-id}`` when *project_id* is set
       (respects ``$XDG_CACHE_HOME``).
    3. ``orc_dir`` when neither is configured (legacy in-tree layout).
    """


_config: Config | None = None


def init(orc_dir: Path, repo_root: Path | None = None) -> Config:
    """Create and store the module-level :class:`Config` singleton.

    *repo_root* is the project root (where ``README.md`` and git live).
    When omitted it falls back to ``orc_dir.parent``, which is correct
    for the common case where the config dir sits directly inside the project
    root (e.g. ``{project}/.orc/``).

    ``.env`` is always resolved relative to ``Path.cwd()`` so that orc reads
    the credentials of the project you are running it *from*, regardless of
    where the config dir lives.

    When ``project-id`` is present in ``config.yaml`` the mutable state
    (board, visions, task files) is stored under
    ``~/.cache/orc/projects/{project_id}/`` rather than inside ``.orc/``.
    Projects without a ``project-id`` continue to use the old in-tree layout
    for backward compatibility.
    """
    global _config

    orc_yaml = load_orc_config(orc_dir)
    work_dev_branch = orc_yaml.get("orc-dev-branch", "dev")
    branch_prefix = orc_yaml.get("orc-branch-prefix", "")
    raw_base = orc_yaml.get("orc-worktree-base", str(orc_dir / "worktrees"))
    worktree_base = Path(raw_base).expanduser().resolve()
    # TODO: move chat.log into logs too
    raw_log_dir = orc_yaml.get("orc-log-dir", str(orc_dir / "logs"))
    log_dir = Path(raw_log_dir).expanduser().resolve()
    raw_exclude = orc_yaml.get("orc-todo-scan-exclude", [".orc"])
    todo_scan_exclude = tuple(raw_exclude) if isinstance(raw_exclude, list) else (raw_exclude,)

    project_id = str(orc_yaml.get("project-id", ""))
    raw_cache_dir = orc_yaml.get("orc-cache-dir", "").strip()
    if raw_cache_dir:
        cache_dir = Path(raw_cache_dir).expanduser().resolve()
    elif project_id:
        cache_dir = _orc_cache_root() / project_id
    else:
        cache_dir = orc_dir
    work_dir = cache_dir / "work"
    vision_dir = cache_dir / "vision"

    _config = Config(
        orc_dir=orc_dir,
        repo_root=(repo_root or orc_dir.parent).resolve(),
        work_dir=work_dir,
        board_file=work_dir / "board.yaml",
        vision_dir=vision_dir,
        roles_dir=orc_dir / "roles",
        env_file=Path.cwd() / ".env",
        dev_worktree=worktree_base / work_dev_branch,
        worktree_base=worktree_base,
        work_dev_branch=work_dev_branch,
        branch_prefix=branch_prefix,
        log_dir=log_dir,
        todo_scan_exclude=todo_scan_exclude,
        project_id=project_id,
        cache_dir=cache_dir,
    )
    # Reinitialise the board manager to match the new config.
    import orc.board as _board  # noqa: PLC0415

    _board.init_manager()
    return _config


def get() -> Config:
    """Return the current :class:`Config`.

    Raises :class:`RuntimeError` if :func:`init` has not been called yet.
    """
    if _config is None:
        raise RuntimeError(
            "orc.config.get() called before init(). "
            "Call orc.config.init(orc_dir) during CLI bootstrap."
        )
    return _config


# ── Discovery helpers ─────────────────────────────────────────────────────


def find_config_dir(base: Path | None = None) -> Path | None:
    """Find the orc configuration directory.

    Resolution order:
    1. ``ORC_DIR`` environment variable (absolute path, used as-is).
    2. ``{base}/.orc/`` — the canonical name.

    *base* defaults to ``Path.cwd()`` when omitted.
    Returns the directory if it exists, or ``None`` otherwise.
    """
    env = os.environ.get("ORC_DIR", "").strip()
    if env:
        return Path(env).resolve()
    candidate = (base or Path.cwd()).resolve() / ".orc"
    return candidate if candidate.is_dir() else None


def load_orc_config(orc_dir: Path) -> dict:
    """Load ``config.yaml`` from *orc_dir*.

    Returns an empty dict if the file is absent or unreadable.
    """
    config_file = orc_dir / "config.yaml"
    if not config_file.exists():
        return {}
    try:
        return yaml.safe_load(config_file.read_text()) or {}
    except Exception:
        logger.warning("failed to parse orc config.yaml", path=str(config_file))
        return {}


@lru_cache(maxsize=1)
def _load_placeholders() -> frozenset[str]:
    """Read unfilled placeholder values from .env.example (cached)."""
    values: set[str] = {""}
    env_example = _ORC_CFG_TEMPLATE / ".env.example"
    for line in env_example.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            val = line.split("=", 1)[1].strip()
            if val and ("your-" in val or val.endswith("-here")):
                values.add(val)
    return frozenset(values)


def validate_env() -> list[str]:
    """Check that all required .env variables are present and not placeholders."""
    cfg = get()
    placeholders = _load_placeholders()
    errors: list[str] = []

    if not cfg.env_file.exists():
        errors.append(
            f".env not found at {cfg.env_file}. "
            "Copy .env.example to .env and fill in your credentials."
        )
        return errors

    # Telegram is optional — no validation here; orc works without it.
    # (see src/orc/telegram.py for graceful-degradation behaviour)

    ai_cli = os.environ.get("COLONY_AI_CLI", "").strip().lower()
    if not ai_cli or ai_cli in placeholders:
        errors.append("COLONY_AI_CLI is not set. Valid values: copilot, claude.")
    elif ai_cli not in {"copilot", "claude"}:
        errors.append(f"COLONY_AI_CLI={ai_cli!r} is not supported. Valid values: copilot, claude.")

    if ai_cli == "claude":
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key or key in placeholders:
            errors.append(
                "ANTHROPIC_API_KEY is not set. "
                "For claude, set it to your Anthropic API key in .env."
            )
    else:
        gh_token = os.environ.get("GH_TOKEN", "").strip()
        if not gh_token or gh_token in placeholders:
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
