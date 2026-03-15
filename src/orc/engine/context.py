"""orc – agent context building and invocation."""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

import structlog
import yaml

import orc.config as _cfg
import orc.git.core as _git
from orc.ai import invoke as inv
from orc.coordination.state import BoardStateManager
from orc.messaging import telegram as tg
from orc.squad import AgentRole

logger = structlog.get_logger(__name__)

_DEFAULT_MODEL = "claude-sonnet-4.6"
_BLOCKED_TIMEOUT = 3600.0  # seconds before giving up on a human reply
_CHAT_WINDOW_SIZE = 50  # max recent messages to keep in full


def _read_adrs(*, summarize: bool = False) -> str:
    """Read ADRs from ``docs/adr/``.

    When *summarize* is True, include only the title, status, and first
    non-empty paragraph of each ADR (for coder/QA who only need the gist).
    """
    adr_dir = _cfg.get().repo_root / "docs" / "adr"
    parts: list[str] = []
    if not adr_dir.exists():
        return "_No ADRs found._"
    for adr_file in sorted(adr_dir.glob("*.md")):
        if adr_file.name == "README.md":
            continue
        if summarize:
            parts.append(_summarize_adr(adr_file))
        else:
            parts.append(f"### {adr_file.name}\n\n{adr_file.read_text()}")
    return "\n\n---\n\n".join(parts) if parts else "_No ADRs found._"


def _summarize_adr(path: Path) -> str:
    """Return a compact summary of a single ADR file.

    Extracts the title (first ``#`` heading), status line, and the first
    non-empty paragraph after any front-matter or heading block.
    """
    text = path.read_text()
    lines = text.splitlines()

    title = path.stem
    status = ""
    first_para: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not title or title == path.stem:
            m = re.match(r"^#+\s+(.+)", stripped)
            if m:
                title = m.group(1)
                continue
        if stripped.lower().startswith("**status"):
            status = stripped
            continue
        if stripped == "---" or stripped == "":
            if first_para:
                break
            continue
        if stripped.startswith("#"):
            if first_para:
                break
            continue
        first_para.append(stripped)

    summary = f"### {path.name}\n\n**{title}**"
    if status:
        summary += f" — {status}"
    if first_para:
        summary += f"\n\n{' '.join(first_para)}"
    summary += f"\n\n_Full text: `docs/adr/{path.name}`_"
    return summary


# ---- Shared-doc extraction helpers ----------------------------------------

# README.md section headings that are irrelevant to agents.
_README_SKIP_HEADINGS = frozenset(
    {
        "installation",
        "quick start",
        "bootstrap",
        ".env",
        "configuration",
        "environment variables",
        "config file",
    }
)


def _extract_readme(full_text: str) -> str:
    """Return a trimmed README keeping only sections useful to agents."""
    return _keep_sections(full_text, skip=_README_SKIP_HEADINGS)


# CONTRIBUTING.md section headings irrelevant to agents.
_CONTRIBUTING_AGENT_SECTIONS: dict[str, frozenset[str]] = {
    AgentRole.CODER: frozenset(
        {
            "the development loop (tdd)",
            "committing",
            "package layout",
        }
    ),
    AgentRole.QA: frozenset(
        {
            "the development loop (tdd)",
            "committing",
            "other useful recipes",
            "package layout",
        }
    ),
    AgentRole.PLANNER: frozenset(
        {
            "package layout",
            "writing an adr",
        }
    ),
}


def _extract_contributing(full_text: str, role: str) -> str:
    """Return only the CONTRIBUTING sections relevant to *role*."""
    keep = _CONTRIBUTING_AGENT_SECTIONS.get(role)
    if keep is None:
        return full_text
    return _keep_sections(full_text, keep=keep)


def _keep_sections(
    text: str,
    *,
    skip: frozenset[str] | None = None,
    keep: frozenset[str] | None = None,
) -> str:
    """Filter Markdown *text* by ``##``-level headings.

    If *skip* is provided, drop sections whose heading matches (case-insensitive).
    If *keep* is provided, retain **only** matching sections (plus any preamble
    before the first heading).
    """
    lines = text.splitlines(keepends=True)
    result: list[str] = []
    current_heading: str | None = None
    include = True
    preamble = True

    for line in lines:
        heading_match = re.match(r"^##\s+(.+)", line)
        if heading_match:
            preamble = False
            current_heading = heading_match.group(1).strip().lower()
            if skip is not None:
                include = current_heading not in skip
            elif keep is not None:
                include = current_heading in keep
        if preamble or include:
            result.append(line)

    return "".join(result).strip()


# ---- Chat-history windowing -----------------------------------------------

_AGENT_STATE_RE = re.compile(r"^\[.+?\]\(.+?\)")


def _window_chat(chat_text: str, *, max_recent: int = _CHAT_WINDOW_SIZE) -> str:
    """Trim chat history to *max_recent* full messages.

    Older messages are kept only if they look like agent state-transition
    lines (``[role](state) ...``).  Everything else is dropped and replaced
    with a ``[... N older messages trimmed ...]`` notice.
    """
    if not chat_text:
        return chat_text
    lines = chat_text.splitlines()
    if len(lines) <= max_recent:
        return chat_text

    old = lines[:-max_recent]
    recent = lines[-max_recent:]

    kept: list[str] = []
    trimmed = 0
    for line in old:
        if _AGENT_STATE_RE.match(line.strip()):
            kept.append(line)
        else:
            trimmed += 1

    if trimmed:
        kept.append(f"\n[... {trimmed} older messages trimmed ...]\n")

    return "\n".join(kept + recent)


def _scan_todos(root: Path) -> list[dict]:
    """Scan *root* for ``#TO-DO`` and ``#FIX-ME`` comments using ``git grep``.

    Returns a list of ``{"file": str, "line": int, "tag": str, "text": str}``
    dicts, one per matching line.  Returns an empty list when *root* is not a
    git repository or when the command fails for any other reason.

    Paths listed in ``Config.todo_scan_exclude`` (YAML key
    ``orc-todo-scan-exclude``, default ``[".orc"]``) are excluded so that orc
    infrastructure files (role prompts, board, config) are not reported as
    action items.
    """
    exclude = _cfg.get().todo_scan_exclude
    pathspecs = [f":!{p}" for p in exclude]
    try:
        result = subprocess.run(
            [
                "git",
                "grep",
                "-n",
                "-I",
                "--no-color",
                "-E",
                r"^\s*#\s*(TODO|FIXME)",
                "--",
                *pathspecs,
            ],
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


def _strip_frontmatter(raw: str) -> str:
    """Strip YAML frontmatter (delimited by ``---``) from *raw* text."""
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            raw = raw[end + 4 :].lstrip("\n")
    return raw


def _parse_role_dir(role_dir: Path) -> str:
    """Load a role from a directory.

    ``_main.md`` is loaded first (if present), then all remaining ``*.md``
    files in alphabetical order.  YAML frontmatter is stripped from every
    file.  Returns a fallback string when the directory contains no Markdown.
    """
    parts: list[str] = []
    main_file = role_dir / "_main.md"
    if main_file.exists():
        parts.append(_strip_frontmatter(main_file.read_text()))
    for md_file in sorted(role_dir.glob("*.md")):
        if md_file.name == "_main.md":
            continue
        parts.append(_strip_frontmatter(md_file.read_text()))
    return "\n\n".join(parts) if parts else f"You are the {role_dir.name} agent."


def _parse_role_file(agent_name: str) -> str:
    """Read a role definition and return its content.

    Supports two formats, checked in this order for each search directory:

    1. **Directory** – ``roles/{agent}/``: ``_main.md`` is loaded first,
       then the remaining ``*.md`` files alphabetically.  Each file's YAML
       frontmatter is stripped before concatenation.
    2. **Single file** – ``roles/{agent}.md``: loaded as before, with YAML
       frontmatter stripped.

    Search order: project-level ``ROLES_DIR`` before the package-bundled
    ``_PACKAGE_ROLES_DIR``.
    """
    for base_dir in (_cfg.get().roles_dir, _cfg._PACKAGE_ROLES_DIR):
        role_dir = base_dir / agent_name
        if role_dir.is_dir():
            return _parse_role_dir(role_dir)
        role_file = base_dir / f"{agent_name}.md"
        if role_file.exists():
            return _strip_frontmatter(role_file.read_text())
    return f"You are the {agent_name} agent."


def _role_symbol(role: str) -> str:
    """Return the symbol declared in the role file's frontmatter, or '' if absent.

    For directory-format roles, the frontmatter is read from ``_main.md``.
    """
    for directory in (_cfg.get().roles_dir, _cfg._PACKAGE_ROLES_DIR):
        role_dir = directory / role
        if role_dir.is_dir():
            role_file = role_dir / "_main.md"
        else:
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
    board: BoardStateManager | None = None,
    extra: str = "",
    worktree: Path | None = None,
    agent_id: str | None = None,
    model: str | None = None,
) -> tuple[str, str]:
    """Return ``(model, context)`` for the given agent.

    *board* is the coordination state manager.  When omitted (e.g. from CLI
    commands like ``orc merge`` that run outside of ``orc run``), a fresh
    :class:`BoardStateManager` is created from the current config.
    """
    if board is None:
        board = BoardStateManager(_cfg.get().orc_dir)
    resolved_model = model or _DEFAULT_MODEL
    role = _parse_role_file(agent_name)

    dev_worktree = _git._ensure_dev_worktree()
    cfg = _cfg.get()
    try:
        agents_rel = cfg.orc_dir.relative_to(cfg.repo_root)
    except ValueError:
        agents_rel = Path(cfg.orc_dir.name)

    # -- shared docs (trimmed per role) ------------------------------------
    readme_path = cfg.repo_root / "README.md"
    readme_raw = readme_path.read_text() if readme_path.exists() else ""
    readme = _extract_readme(readme_raw)

    contributing_path = cfg.repo_root / "CONTRIBUTING.md"
    contributing_raw = contributing_path.read_text() if contributing_path.exists() else ""
    contributing = _extract_contributing(contributing_raw, agent_name)

    # ADRs: full for planner, summarised for coder/QA
    adrs = _read_adrs(summarize=agent_name != AgentRole.PLANNER)

    chat = tg.messages_to_text(messages)
    chat = _window_chat(chat)

    # Board: scoped to active task for coder/QA
    active_task = board.active_task_name()
    if agent_name in (AgentRole.CODER, AgentRole.QA) and active_task:
        plans = board.read_work_summary(active_only=active_task)
    else:
        plans = board.read_work_summary()

    feature_branch = _git._feature_branch(active_task) if active_task else None
    feature_wt = _git._feature_worktree_path(active_task) if active_task else None

    id_line = (
        f"\n**Your agent ID**: `{agent_id}` — use this ID in all Telegram messages.\n"
        if agent_id
        else ""
    )

    if agent_name == AgentRole.CODER and feature_branch:
        git_info = (
            f"Your branch: `{feature_branch}` (cut from `main`)\n"
            f"Your worktree: `{feature_wt}` — all edits and git commands go here\n"
            f"Dev branch: `{cfg.work_dev_branch}` (managed by planner and QA — do not touch)\n"
            f"Main worktree: `{cfg.repo_root}` (human's workspace — do not touch)\n\n"
            f"Work exclusively in your feature worktree (`{feature_wt}`). "
            f"Commit to `{feature_branch}` only. "
            f"The orchestrator will merge your branch into "
            f"`{cfg.work_dev_branch}` after QA passes."
        )
    elif agent_name == AgentRole.QA and feature_branch:
        git_info = (
            f"Branch to review: `{feature_branch}`\n"
            f"Feature worktree: `{feature_wt}`\n"
            f"Dev branch: `{cfg.work_dev_branch}`\n"
            f"Dev worktree: `{dev_worktree}`\n"
            f"Main worktree: `{cfg.repo_root}` (human's workspace — do not touch)\n\n"
            f"Review `{feature_branch}` against `{cfg.work_dev_branch}` "
            f"(e.g. `git diff {cfg.work_dev_branch}...{feature_branch}`).\n"
            f"Run in the dev worktree (`{dev_worktree}`). "
            f"**Do NOT merge** — the orchestrator merges after you signal `passed`."
        )
    else:
        git_info = (
            f"Dev branch: `{cfg.work_dev_branch}`\n"
            f"Dev worktree path: `{dev_worktree}`\n"
            f"Main worktree path: `{cfg.repo_root}` (human's workspace — do not touch)\n\n"
            f"All file edits and git commands must be performed inside the dev "
            f"worktree (`{dev_worktree}`)."
        )
        if feature_branch:
            git_info += f"\nActive feature branch: `{feature_branch}` (coder's branch)"

    extra_section = f"## Current task\n\n{extra}\n\n" if extra else ""

    todos_section = ""
    if agent_name == AgentRole.PLANNER:
        todos = _scan_todos(cfg.repo_root)
        todos_section = f"### Code TODOs and FIXMEs\n\n{_format_todos(todos)}\n\n"

    context = (
        f"{role}\n"
        f"{id_line}\n"
        "---\n\n"
        f"{extra_section}"
        "## Shared context\n\n"
        f"### Git workflow\n\n{git_info}\n\n"
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


def _boot_message_body(agent_id: str, board: BoardStateManager) -> str:
    """Build the role-specific body text for a boot message."""
    role, _ = tg.parse_agent_id(agent_id)
    open_tasks = board.get_tasks()
    first_task = (
        (open_tasks[0]["name"] if isinstance(open_tasks[0], dict) else str(open_tasks[0]))
        if open_tasks
        else None
    )

    if role == AgentRole.PLANNER:
        if first_task:
            return f"planning {first_task}."
        if board.get_pending_visions():
            return "translating vision docs."
        return "no open tasks on board."

    if role == AgentRole.CODER:
        if first_task:
            return f"picking up work/{first_task}."
        return "no open tasks on board."

    if role == AgentRole.QA:
        if first_task:
            task_stem = re.sub(r"\.md$", "", first_task)
            return f"reviewing feat/{task_stem}."
        return "no open tasks on board."

    # Default fallback: list all open tasks
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
