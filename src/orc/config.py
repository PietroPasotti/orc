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
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)

# ── Package-relative constants (truly static, safe at import time) ─────────
_PACKAGE_DIR = Path(__file__).parent
_PACKAGE_AGENTS_DIR = _PACKAGE_DIR / "agents"
_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_ORC_CFG_TEMPLATE = _TEMPLATES_DIR / "default" / "orc_cfg"


# ── Orc config YAML model ─────────────────────────────────────────────────


class OrcConfig(BaseModel):
    """Validated representation of ``config.yaml`` keys."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    orc_dev_branch: str = Field(default="dev", alias="orc-dev-branch")
    """Integration branch name."""
    orc_main_branch: str = Field(default="", alias="orc-main-branch")
    """Main/stable branch name (auto-detected from git if empty)."""
    orc_branch_prefix: str = Field(default="", alias="orc-branch-prefix")
    """Optional prefix for all orc-owned branches."""
    orc_worktree_base: str | None = Field(default=None, alias="orc-worktree-base")
    """Base directory for git worktrees."""
    orc_log_dir: str | None = Field(default=None, alias="orc-log-dir")
    """Override the log directory."""
    orc_todo_scan_exclude: list[str] | str = Field(
        default_factory=lambda: [".orc"], alias="orc-todo-scan-exclude"
    )
    """Path patterns excluded from ``#TODO`` / ``#FIXME`` scans."""
    default_model: str = Field(default="claude-sonnet-4.6", alias="default-model")
    """Default AI model used when a squad profile does not specify one."""
    human_reply_wait_timeout: float = Field(default=3600.0, alias="human-reply-wait-timeout")
    """Seconds to wait for a human Telegram reply before giving up."""
    chat_window_size: int = Field(default=50, alias="chat-window-size")
    """Maximum number of recent messages kept in full in the chat window."""


# ── Immutable config object ───────────────────────────────────────────────
@dataclass(frozen=True)
class Config:
    """All resolved paths and settings for the current orc session."""

    orc_dir: Path
    repo_root: Path
    work_dir: Path
    board_file: Path
    vision_dir: Path
    """Directory containing vision documents — ``orc_dir/vision``."""
    agents_dir: Path
    env_file: Path
    dev_worktree: Path
    worktree_base: Path
    work_dev_branch: str
    branch_prefix: str
    log_dir: Path
    chat_log: Path
    """Path to the local chat log file (JSONL)."""
    todo_scan_exclude: tuple[str, ...]
    """Path patterns excluded from ``#TODO`` / ``#FIXME`` scans (git pathspec format)."""
    api_socket_path: Path
    """Unix domain socket for the coordination API — ``orc_dir/run/orc.sock``."""
    default_model: str
    """Default AI model used when a squad profile does not specify one."""
    human_reply_wait_timeout: float
    """Seconds to wait for a human Telegram reply before giving up."""
    chat_window_size: int
    """Maximum number of recent messages kept in full in the chat window."""
    main_branch: str = "main"
    """Name of the main branch in the repository, used as the default target for dev merges."""

    def feature_branch(self, task_name: str) -> str:
        """Return the feature branch name for *task_name*.

        When ``orc-branch-prefix`` is set the branch is prefixed, e.g.
        ``"orc/feat/0001-foo"``; without a prefix: ``"feat/0001-foo"``.
        """
        branch = f"feat/{Path(task_name).stem}"
        return f"{self.branch_prefix}/{branch}" if self.branch_prefix else branch

    def feature_worktree_path(self, task_name: str) -> Path:
        """Return the expected filesystem path of the feature worktree."""
        return self.worktree_base / Path(task_name).stem


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

    Mutable state (board, visions, task files) lives inside ``orc_dir``
    under ``work/`` and ``vision/`` respectively.  These directories are
    excluded from git via ``.orc/.gitignore``.
    """
    global _config

    orc_yaml = load_orc_config(orc_dir)

    work_dev_branch = orc_yaml.orc_dev_branch
    branch_prefix = orc_yaml.orc_branch_prefix
    raw_base = orc_yaml.orc_worktree_base or str(orc_dir / "worktrees")
    worktree_base = Path(raw_base).expanduser().resolve()
    raw_log_dir = orc_yaml.orc_log_dir or str(orc_dir / "logs")
    log_dir = Path(raw_log_dir).expanduser().resolve()

    raw_exclude = orc_yaml.orc_todo_scan_exclude
    todo_scan_exclude = tuple(raw_exclude) if isinstance(raw_exclude, list) else (raw_exclude,)

    work_dir = orc_dir / "work"
    vision_dir = orc_dir / "vision"

    _config = Config(
        orc_dir=orc_dir,
        repo_root=(repo_root or orc_dir.parent).resolve(),
        work_dir=work_dir,
        board_file=work_dir / "board.yaml",
        vision_dir=vision_dir,
        agents_dir=orc_dir / "agents",
        env_file=Path.cwd() / ".env",
        dev_worktree=worktree_base / work_dev_branch,
        worktree_base=worktree_base,
        work_dev_branch=work_dev_branch,
        branch_prefix=branch_prefix,
        log_dir=log_dir,
        chat_log=log_dir / "chat.log",
        todo_scan_exclude=todo_scan_exclude,
        api_socket_path=orc_dir / "run" / "orc.sock",
        default_model=orc_yaml.default_model,
        human_reply_wait_timeout=orc_yaml.human_reply_wait_timeout,
        chat_window_size=orc_yaml.chat_window_size,
    )
    # Reinitialise the board manager to match the new config.
    import orc.coordination.board as _board  # noqa: PLC0415

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


def load_orc_config(orc_dir: Path) -> OrcConfig:
    """Load and validate ``config.yaml`` from *orc_dir*.

    Returns an :class:`OrcConfig` with all defaults applied if the file is
    absent or unreadable.
    """
    config_file = orc_dir / "config.yaml"
    if not config_file.exists():
        return OrcConfig()
    try:
        raw: dict[str, object] = yaml.safe_load(config_file.read_text()) or {}
        return OrcConfig.model_validate(raw)
    except Exception:
        logger.warning("failed to parse orc config.yaml", path=str(config_file))
        return OrcConfig()


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
    _load_placeholders()
    errors: list[str] = []

    if not cfg.env_file.exists():
        errors.append(
            f".env not found at {cfg.env_file}. "
            "Copy .env.example to .env and fill in your credentials."
        )
        return errors

    # Telegram is optional — no validation here; orc works without it.
    # (see src/orc/telegram.py for graceful-degradation behaviour)

    # Validate LLM API credentials.
    # The internal backend reads the provider from squad config; for env
    # validation we check the most common credential sources.
    gemini_key = os.environ.get("GEMINI_API_TOKEN", "").strip()
    gh_token = os.environ.get("GH_TOKEN", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()

    has_any_key = bool(gemini_key) or bool(openai_key)
    if not has_any_key and not gh_token:
        # Check GH token fallback chain.
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
                    "No LLM API key found. Set one of: "
                    "GEMINI_API_TOKEN, GH_TOKEN, or OPENAI_API_KEY in .env."
                )

    return errors
