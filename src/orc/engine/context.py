"""orc – agent context building and invocation."""

from __future__ import annotations

import re
import subprocess
import time
import typing
from dataclasses import dataclass
from pathlib import Path

import structlog
import yaml

import orc.config as _cfg
from orc.coordination.state import BoardStateManager
from orc.messaging import telegram as tg
from orc.messaging.messages import (
    ChatMessage,
)
from orc.messaging.messages import (
    is_agent_message as _is_agent_message,
)
from orc.squad import AgentRole, ReviewThreshold

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class TodoItem:
    """A single TODO or FIXME comment found in the codebase."""

    file: str
    line: int
    tag: str
    text: str


# ---- Chat-history windowing -----------------------------------------------

_AGENT_STATE_RE = re.compile(r"^\[.+?\]\(.+?\)")


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


def _extract_steps_section(task_md: str) -> str:
    """Extract and return the raw content of the ``## Steps`` section from *task_md*.

    Returns an empty string when the section is absent.
    """
    match = re.search(r"## Steps\n\n(.*?)(?=\n## |\Z)", task_md, re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


def build_agent_context(
    role: AgentRole,
    board: BoardStateManager,
    agent_id: str,
    task_name: str | None = None,  # this can be None only for planner
    plain: bool = False,  # plain: return only the base context, no agent-specific instructions
    review_threshold: ReviewThreshold | None = None,
) -> tuple[str, str]:
    """Return the system and user prompts for the given agent.

    The context is kept intentionally compact: only live runtime data is
    injected into the user prompt. Static documentation (README, CONTRIBUTING,
    ADRs) and full role instructions are inlined into the system prompt.

    *task_name* is the specific task assigned to this agent (coder/QA only).
    The dispatcher always knows which task it is dispatching; it must pass it
    explicitly rather than letting context.py guess from board state.
    """
    cfg = _cfg.get()

    # System prompt construction
    system_parts: list[str] = []

    # Read shared instructions if they exist.
    for directory in (cfg.agents_dir, _cfg._PACKAGE_AGENTS_DIR):
        shared_path = directory / "_shared" / "_main.md"
        if shared_path.is_file():
            system_parts.append(shared_path.read_text())
            break  # Project-level takes precedence

    # Read role-specific instructions.
    role_found = False
    for directory in (cfg.agents_dir, _cfg._PACKAGE_AGENTS_DIR):
        role_dir = directory / role
        if role_dir.is_dir():
            role_file = role_dir / "_main.md"
        else:
            role_file = directory / f"{role}.md"

        if role_file.is_file():
            system_parts.append(role_file.read_text())
            role_found = True
            break  # Project-level takes precedence

    if not role_found:
        logger.warning("no instruction file found for role", role=role)

    system_prompt = "\n\n---\n\n".join(system_parts)
    if not system_prompt.strip():
        system_prompt = (
            "You are an AI agent working on a software project. Follow your instructions carefully."
        )

    # User prompt construction
    user_prompt = f"Your `agent ID` is: **`{agent_id}`**."

    if plain:
        return system_prompt, user_prompt

    user_prompt += "\n\n# Additional Context:\n\n"

    dev_branch = cfg.work_dev_branch
    dev_worktree = cfg.dev_worktree
    feature_branch = cfg.feature_branch(task_name) if task_name else None
    feature_wt = cfg.feature_worktree_path(task_name) if task_name else None

    match role:
        case AgentRole.PLANNER:
            planner_ctx = f"""
            Dev branch: `{dev_branch}`
            Dev worktree path: `{dev_worktree}`
            Main worktree path: `{cfg.repo_root}` (human's workspace — do not touch)

            All file edits and git commands must be performed inside the dev
            worktree (`{dev_worktree}`).
            """
            if feature_branch:
                planner_ctx += f"\nActive feature branch: `{feature_branch}` (coder's branch)"
            if pending_visions := board.get_pending_visions():
                items = "\n".join(
                    f"- `{name}` — call the `get_vision` MCP tool with"
                    f" `vision_filename={name!r}` to read the full document"
                    for name in pending_visions
                )
                planner_ctx += f"### Pending visions\n\n{items}\n\n"
            if blocked_tasks := board.get_blocked_tasks():
                items = "\n".join(
                    f"- `{name}` — call the `get_task` MCP tool with `task_filename={name!r}`"
                    " to view full details and conversation"
                    for name in blocked_tasks
                )
                planner_ctx += f"### Blocked tasks\n\n{items}\n\n"
            todos = _scan_todos(cfg.dev_worktree)
            planner_ctx += f"### Code TODOs and FIXMEs\n\n{_format_todos(todos)}\n\n"
            user_prompt += planner_ctx

        case AgentRole.CODER:
            assert feature_branch is not None
            assert feature_wt is not None

            user_prompt += f"""
            Your branch: `{feature_branch}` (cut from `{dev_branch}`)
            Your worktree: `{feature_wt}` — all edits and git commands go here
            Dev branch: `{dev_branch}` (managed by planner and QA — do not touch)
            Work exclusively in your feature worktree.
            Commit to `{feature_branch}` **EXCLUSIVELY**.
            The orchestrator will merge your branch into
            `{dev_branch}` after QA passes.

            ⚠️ **Environment note:** Your worktree does NOT have its own
            virtual environment. Use `just test` or `uv run pytest` — never
            bare `pytest` or other tool commands, as they may resolve to a
            different Python installation.
            """

            if task_name:
                try:
                    task_md = board.read_task_content(task_name)
                    steps = _extract_steps_section(task_md)
                    if steps:
                        user_prompt += (
                            f"\n## Steps\n\n{steps}\n\n"
                            "Mark each step `- [x]` in the task file as you complete it.\n"
                        )
                except FileNotFoundError:
                    pass

        case AgentRole.QA:
            assert feature_branch is not None
            assert feature_wt is not None

            threshold = review_threshold or ReviewThreshold.LOW
            user_prompt += f"""
            Task: `{task_name}`
            Branch to review: `{feature_branch}`
            Feature worktree: `{feature_wt}`
            Dev branch: `{dev_branch}`
            Dev worktree: `{dev_worktree}`
            Main worktree: `{cfg.repo_root}` (human's workspace — do not touch)
            Review threshold: `{threshold.value}`

            Review `{feature_branch}` against `{dev_branch}`
            (e.g. `git diff {dev_branch}...{feature_branch}`).

            Run in the dev worktree (`{dev_worktree}`).
            **Do NOT merge** — the orchestrator merges only if you
            approve this work by signalling `passed`.
            """

        case AgentRole.MERGER:
            assert feature_branch is not None
            assert task_name is not None
            # Extract four-digit task code from name like "0046-add-repr.md"
            task_code = task_name.split("-", 1)[0]

            user_prompt += f"""
            Task: `{task_name}` (task code: `{task_code}`)
            Feature branch to merge: `{feature_branch}`
            Dev branch: `{dev_branch}`
            Dev worktree: `{dev_worktree}` — all operations go here
            Main worktree: `{cfg.repo_root}` (human's workspace — do not touch)

            Merge `{feature_branch}` into `{dev_branch}` using
            `git merge --no-ff` in the dev worktree (`{dev_worktree}`).
            Resolve any conflicts, then call `close_merge(task_code="{task_code}", message="...")`.
            """

        case _:
            typing.assert_never(role)

    return system_prompt, user_prompt.strip()


def wait_for_human_reply(
    messages_snapshot: list[ChatMessage],
    *,
    initial_delay: float = 5.0,
    backoff_factor: float = 2.0,
    max_delay: float = 300.0,
    timeout: float | None = None,
) -> str:
    """Poll Telegram until a new human message appears after *messages_snapshot*."""
    if timeout is None:
        timeout = _cfg.get().human_reply_wait_timeout
    _tg_svc = tg.TelegramMessagingService()
    if not _tg_svc.is_configured():
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
        for msg in _tg_svc.get_messages():
            key = (msg.date, msg.text)
            if key not in seen and not _is_agent_message(msg.text):
                return msg.text
        delay = min(delay * backoff_factor, max_delay)
