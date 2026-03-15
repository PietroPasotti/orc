"""orc – agent context building and invocation."""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import structlog
import yaml

import orc.config as _cfg
from orc.ai import invoke as inv
from orc.coordination.state import BoardStateManager
from orc.git import Git as _Git
from orc.messaging import telegram as tg
from orc.messaging.messages import (
    ChatMessage,
)
from orc.messaging.messages import (
    is_agent_message as _is_agent_message,
)
from orc.messaging.messages import (
    messages_to_text as _messages_to_text,
)
from orc.messaging.messages import (
    parse_agent_id as _parse_agent_id,
)
from orc.squad import AgentRole

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class TodoItem:
    """A single TODO or FIXME comment found in the codebase."""

    file: str
    line: int
    tag: str
    text: str


_DEFAULT_MODEL = "claude-sonnet-4.6"
_BLOCKED_TIMEOUT = 3600.0  # seconds before giving up on a human reply
_CHAT_WINDOW_SIZE = 50  # max recent messages to keep in full

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


def _scan_todos(root: Path) -> list[TodoItem]:
    """Scan *root* for ``#TO-DO`` and ``#FIX-ME`` comments using ``git grep``.

    Returns a list of :class:`TodoItem` objects, one per matching line.
    Returns an empty list when *root* is not a
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

    todos: list[TodoItem] = []
    for raw_line in result.stdout.splitlines():
        parts = raw_line.split(":", 2)
        if len(parts) < 3:
            continue
        filepath, lineno_str, text_content = parts[0], parts[1], parts[2]
        try:
            lineno = int(lineno_str)
        except ValueError:
            continue
        tag = "FIXME" if "FIXME" in text_content.upper() else "TODO"
        todos.append(TodoItem(file=filepath, line=lineno, tag=tag, text=text_content.strip()))
    return todos


def _format_todos(todos: list[TodoItem]) -> str:
    """Format *todos* (from :func:`_scan_todos`) as a Markdown table."""
    if not todos:
        return "_No TODO or FIXME comments found in the codebase._"
    rows = ["| File | Line | Tag | Comment |", "|------|------|-----|---------|"]
    for t in todos:
        rows.append(f"| `{t.file}` | {t.line} | `{t.tag}` | {t.text} |")
    return "\n".join(rows)


def _role_symbol(role: str) -> str:
    """Return the symbol declared in the role file's frontmatter, or '' if absent.

    For directory-format roles, the frontmatter is read from ``_main.md``.
    """
    for directory in (_cfg.get().agents_dir, _cfg._PACKAGE_AGENTS_DIR):
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
    messages: list[ChatMessage],
    board: BoardStateManager | None = None,
    extra: str = "",
    worktree: Path | None = None,
    agent_id: str | None = None,
    model: str | None = None,
) -> tuple[str, str]:
    """Return ``(model, context)`` for the given agent.

    The context is kept intentionally compact: only live runtime data is
    injected.  Static documentation (README, CONTRIBUTING, ADRs) and full
    role instructions are *not* inlined — the agent is told where to find its
    ``_main.md`` and reads everything from disk itself.

    *board* is the coordination state manager.  When omitted (e.g. from CLI
    commands like ``orc merge`` that run outside of ``orc run``), a fresh
    :class:`BoardStateManager` is created from the current config.
    """
    if board is None:
        board = BoardStateManager(_cfg.get().orc_dir)
    resolved_model = model or _DEFAULT_MODEL

    cfg = _cfg.get()
    _Git(cfg.repo_root).ensure_worktree(cfg.dev_worktree, cfg.work_dev_branch)
    dev_worktree = cfg.dev_worktree
    try:
        agents_rel = cfg.orc_dir.relative_to(cfg.repo_root)
    except ValueError:
        agents_rel = Path(cfg.orc_dir.name)

    role_path = agents_rel / "agents" / agent_name / "_main.md"

    chat = _messages_to_text(messages)
    chat = _window_chat(chat)

    # Board: metadata-only summary (no task content, no comments — agents fetch on demand)
    active_task = board.active_task_name()
    plans = board.read_work_summary()

    feature_branch = cfg.feature_branch(active_task) if active_task else None
    feature_wt = cfg.feature_worktree_path(active_task) if active_task else None

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

    blocked_section = ""
    if agent_name == AgentRole.PLANNER:
        blocked_tasks = board.get_blocked_tasks()
        if blocked_tasks:
            items = "\n".join(
                f"- `{name}` — run `.orc/agent_tools/share/get_task.py {name}`"
                " to view full details and conversation"
                for name in blocked_tasks
            )
            blocked_section = f"### Blocked tasks\n\n{items}\n\n"

    todos_section = ""
    if agent_name == AgentRole.PLANNER:
        todos = _scan_todos(cfg.repo_root)
        todos_section = f"### Code TODOs and FIXMEs\n\n{_format_todos(todos)}\n\n"

    context = (
        f"Your role instructions are at `{role_path}` —"
        " read this file before doing anything else.\n"
        f"{id_line}\n"
        "---\n\n"
        f"{extra_section}"
        "## Runtime context\n\n"
        f"### Git workflow\n\n{git_info}\n\n"
        f"### Chat history (Telegram)\n\n{chat}\n\n"
        f"### Kanban board ({agents_rel}/work/)\n\n{plans}\n"
        f"{blocked_section}"
        f"{todos_section}"
    )
    return resolved_model, context


def wait_for_human_reply(
    messages_snapshot: list[ChatMessage],
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
    seen = frozenset((m.date, m.text) for m in messages_snapshot)
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
            key = (msg.date, msg.text)
            if key not in seen and not _is_agent_message(msg.text):
                return msg.text
        delay = min(delay * backoff_factor, max_delay)


def _boot_message_body(agent_id: str, board: BoardStateManager) -> str:
    """Build the role-specific body text for a boot message."""
    role, _ = _parse_agent_id(agent_id)
    open_tasks = board.get_tasks()
    first_task = open_tasks[0].name if open_tasks else None

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
    names = [t.name for t in open_tasks]
    paths = ", ".join(f"work/{n}" for n in names)
    return f"picking up {paths}."


def invoke_agent(
    agent_name: str, context: str, model: str, worktree: Path | None = None
) -> int:  # pragma: no cover
    """Invoke the configured AI CLI with the agent's full context prompt."""
    cfg = _cfg.get()
    _Git(cfg.repo_root).ensure_worktree(cfg.dev_worktree, cfg.work_dev_branch)
    cwd = worktree or cfg.dev_worktree
    return inv.invoke(context, cwd=cwd, model=model)
