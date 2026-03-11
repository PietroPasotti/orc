"""orc – agent context building and invocation."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import structlog
import yaml

import orc.board as _board
import orc.config as _cfg
import orc.git as _git
from orc import invoke as inv
from orc import telegram as tg

logger = structlog.get_logger(__name__)

_DEFAULT_MODEL = "claude-sonnet-4.6"
_BLOCKED_TIMEOUT = 3600.0  # seconds before giving up on a human reply


def _read_adrs() -> str:
    adr_dir = _cfg.REPO_ROOT / "docs" / "adr"
    parts: list[str] = []
    if not adr_dir.exists():
        return "_No ADRs found._"
    for adr_file in sorted(adr_dir.glob("*.md")):
        if adr_file.name == "README.md":
            continue
        parts.append(f"### {adr_file.name}\n\n{adr_file.read_text()}")
    return "\n\n---\n\n".join(parts) if parts else "_No ADRs found._"


def _scan_todos(root: Path) -> list[dict]:
    """Scan *root* for ``#TODO`` and ``#FIXME`` comments using ``git grep``.

    Returns a list of ``{"file": str, "line": int, "tag": str, "text": str}``
    dicts, one per matching line.  Returns an empty list when *root* is not a
    git repository or when the command fails for any other reason.
    """
    try:
        result = subprocess.run(
            ["git", "grep", "-n", "-I", "--no-color", "-E", r"#\s*(TODO|FIXME)"],
            cwd=root,
            capture_output=True,
            text=True,
        )
    except Exception:
        return []

    todos: list[dict] = []
    for line in result.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        filepath, lineno_str, content = parts[0], parts[1], parts[2]
        try:
            lineno = int(lineno_str)
        except ValueError:
            continue
        tag = "FIXME" if "FIXME" in content.upper() else "TODO"
        todos.append({"file": filepath, "line": lineno, "tag": tag, "text": content.strip()})
    return todos


def _format_todos(todos: list[dict]) -> str:
    """Format *todos* (from :func:`_scan_todos`) as a Markdown table."""
    if not todos:
        return "_No TODO or FIXME comments found in the codebase._"
    rows = ["| File | Line | Tag | Comment |", "|------|------|-----|---------|"]
    for t in todos:
        rows.append(f"| `{t['file']}` | {t['line']} | `{t['tag']}` | {t['text']} |")
    return "\n".join(rows)


def _has_planner_work() -> bool:
    """Return ``True`` if the planner has anything to do.

    The planner has work when either:
    - there are pending vision documents (present in ``AGENTS_DIR/vision/`` but
      not yet tracked on the kanban board), **or**
    - the codebase contains ``#TODO`` / ``#FIXME`` comments.
    """
    vision_dir = _cfg.AGENTS_DIR / "vision"
    if vision_dir.is_dir():
        board = _board._read_board()
        all_task_stems = {
            (t["name"] if isinstance(t, dict) else str(t))
            for tasks in (board.get("open", []), board.get("done", []))
            for t in tasks
        }
        for f in sorted(vision_dir.glob("*.md")):
            if f.name.lower().startswith(".") or f.name.lower() == "readme.md":
                continue
            if not any(stem == f.name or stem.startswith(f.stem) for stem in all_task_stems):
                return True
    return bool(_scan_todos(_cfg.REPO_ROOT))


def _parse_role_file(agent_name: str) -> str:
    """Read a role file and return its content."""
    role_file = _cfg.ROLES_DIR / f"{agent_name}.md"
    if not role_file.exists():
        role_file = _cfg._PACKAGE_ROLES_DIR / f"{agent_name}.md"
    if not role_file.exists():
        return f"You are the {agent_name} agent."

    raw = role_file.read_text()

    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            raw = raw[end + 4 :].lstrip("\n")

    return raw


def _role_symbol(role: str) -> str:
    """Return the symbol declared in the role file's frontmatter, or '' if absent."""
    for directory in (_cfg.ROLES_DIR, _cfg._PACKAGE_ROLES_DIR):
        role_file = directory / f"{role}.md"
        if not role_file.exists():
            continue
        raw = role_file.read_text()
        if not raw.startswith("---"):
            continue
        end = raw.find("\n---", 3)
        if end == -1:
            continue
        fm = yaml.safe_load(raw[3:end]) or {}
        if "symbol" in fm:
            return str(fm["symbol"])
    return ""


def build_agent_context(
    agent_name: str,
    messages: list[dict],
    extra: str = "",
    worktree: Path | None = None,
    agent_id: str | None = None,
    model: str | None = None,
) -> tuple[str, str]:
    """Return ``(model, context)`` for the given agent."""
    resolved_model = model or _DEFAULT_MODEL
    role = _parse_role_file(agent_name)

    dev_worktree = _git._ensure_dev_worktree()
    try:
        agents_rel = _cfg.AGENTS_DIR.relative_to(_cfg.REPO_ROOT)
    except ValueError:
        agents_rel = Path(_cfg.AGENTS_DIR.name)
    orc_readme_path = _cfg.AGENTS_DIR / "README.md"
    orc_readme = orc_readme_path.read_text() if orc_readme_path.exists() else ""
    readme_path = _cfg.REPO_ROOT / "README.md"
    readme = readme_path.read_text() if readme_path.exists() else ""
    contributing_path = _cfg.REPO_ROOT / "CONTRIBUTING.md"
    contributing = contributing_path.read_text() if contributing_path.exists() else ""
    adrs = _read_adrs()
    chat = tg.messages_to_text(messages)
    plans = _board._read_work()

    active_task = _board._active_task_name()
    feature_branch = _git._feature_branch(active_task) if active_task else None
    feature_wt = _git._feature_worktree_path(active_task) if active_task else None

    id_line = (
        f"\n**Your agent ID**: `{agent_id}` — use this ID in all Telegram messages.\n"
        if agent_id
        else ""
    )

    if agent_name == "coder" and feature_branch:
        git_info = (
            f"Your branch: `{feature_branch}` (cut from `main`)\n"
            f"Your worktree: `{feature_wt}` — all edits and git commands go here\n"
            f"Dev branch: `{_cfg.WORK_DEV_BRANCH}` (managed by planner and QA — do not touch)\n"
            f"Main worktree: `{_cfg.REPO_ROOT}` (human's workspace — do not touch)\n\n"
            f"Work exclusively in your feature worktree (`{feature_wt}`). "
            f"Commit to `{feature_branch}` only. "
            f"The orchestrator will merge your branch into "
            f"`{_cfg.WORK_DEV_BRANCH}` after QA passes."
        )
    elif agent_name == "qa" and feature_branch:
        git_info = (
            f"Branch to review: `{feature_branch}`\n"
            f"Feature worktree: `{feature_wt}`\n"
            f"Dev branch: `{_cfg.WORK_DEV_BRANCH}`\n"
            f"Dev worktree: `{dev_worktree}`\n"
            f"Main worktree: `{_cfg.REPO_ROOT}` (human's workspace — do not touch)\n\n"
            f"Review `{feature_branch}` against `{_cfg.WORK_DEV_BRANCH}` "
            f"(e.g. `git diff {_cfg.WORK_DEV_BRANCH}...{feature_branch}`).\n"
            f"Run in the dev worktree (`{dev_worktree}`). "
            f"**Do NOT merge** — the orchestrator merges after you signal `passed`."
        )
    else:
        git_info = (
            f"Dev branch: `{_cfg.WORK_DEV_BRANCH}`\n"
            f"Dev worktree path: `{dev_worktree}`\n"
            f"Main worktree path: `{_cfg.REPO_ROOT}` (human's workspace — do not touch)\n\n"
            f"All file edits and git commands must be performed inside the dev "
            f"worktree (`{dev_worktree}`)."
        )
        if feature_branch:
            git_info += f"\nActive feature branch: `{feature_branch}` (coder's branch)"

    extra_section = f"## Current task\n\n{extra}\n\n" if extra else ""

    todos_section = ""
    if agent_name == "planner":
        todos = _scan_todos(_cfg.REPO_ROOT)
        todos_section = f"### Code TODOs and FIXMEs\n\n{_format_todos(todos)}\n\n"

    context = (
        f"{role}\n"
        f"{id_line}\n"
        "---\n\n"
        f"{extra_section}"
        "## Shared context\n\n"
        f"### Git workflow\n\n{git_info}\n\n"
        f"### Orc workflow documentation ({agents_rel}/README.md)\n\n{orc_readme}\n\n"
        f"### README\n\n{readme}\n\n"
        f"### CONTRIBUTING\n\n{contributing}\n\n"
        f"### Architecture Decision Records\n\n{adrs}\n\n"
        f"### Chat history (Telegram)\n\n{chat}\n\n"
        f"### Kanban board ({agents_rel}/work/)\n\n{plans}\n"
        f"{todos_section}"
    )
    return resolved_model, context


def wait_for_human_reply(
    messages_snapshot: list[dict],
    *,
    initial_delay: float = 5.0,
    backoff_factor: float = 2.0,
    max_delay: float = 300.0,
    timeout: float = _BLOCKED_TIMEOUT,
) -> str:
    """Poll Telegram until a new human message appears after *messages_snapshot*."""
    if not tg.is_configured():
        logger.warning("Telegram not configured — cannot wait for human reply; treating as timeout")
        raise TimeoutError("Telegram not configured; human reply not possible.")
    seen = frozenset((m.get("date", 0), m.get("text", "")) for m in messages_snapshot)
    delay = initial_delay
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"No human reply received within {timeout:.0f}s.")
        actual_delay = min(delay, remaining)
        logger.info("waiting for telegram reply", delay_s=round(actual_delay))
        time.sleep(actual_delay)
        for msg in tg.get_messages():
            key = (msg.get("date", 0), msg.get("text", ""))
            if key not in seen and not tg.is_agent_message(msg.get("text", "")):
                return msg.get("text", "")
        delay = min(delay * backoff_factor, max_delay)


def _boot_message_body() -> str:
    """Build the body text for a (boot) message listing open work items."""
    board = _board._read_board()
    open_tasks = board.get("open", [])
    if not open_tasks:
        return "no open tasks on board."
    names = [(t["name"] if isinstance(t, dict) else str(t)) for t in open_tasks]
    paths = ", ".join(f"work/{n}" for n in names)
    return f"picking up {paths}."


def invoke_agent(
    agent_name: str, context: str, model: str, worktree: Path | None = None
) -> int:  # pragma: no cover
    """Invoke the configured AI CLI with the agent's full context prompt."""
    cwd = worktree or _git._ensure_dev_worktree()
    return inv.invoke(context, cwd=cwd, model=model)
