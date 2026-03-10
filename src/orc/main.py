"""orc – multi-agent orchestrator CLI.

Implements the state machine that governs the multi-agent workflow:

    planner -> coder -> qa -> planner -> ...

The next agent is determined by fetching the most recent agent message from
the Telegram chat channel and applying the transition table.

Usage::

    orc run [--maxloops N] [--dry-run] [--squad NAME] [--config-dir PATH]
    orc status [--config-dir PATH]

Environment variables (via .env)::

    COLONY_TELEGRAM_TOKEN   – Telegram bot token from @BotFather
    COLONY_TELEGRAM_CHAT_ID – Chat/group/channel ID the agents use
"""

import json
import os
import re as _re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import structlog
import typer
import yaml

from orc import dispatcher as _disp
from orc import invoke as inv
from orc import logger as _obs
from orc import telegram as tg
from orc.squad import SquadConfig, load_all_squads, load_squad

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Paths — resolved at startup; can be overridden via --config-dir CLI option
# ---------------------------------------------------------------------------

_PACKAGE_DIR = Path(__file__).parent  # where the package is installed
_PACKAGE_ROLES_DIR = _PACKAGE_DIR / "roles"  # fallback for role files


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

    *repo_root* is the project root (where ``.env``, ``README.md``, and git
    live).  When omitted it falls back to ``agents_dir.parent``, which is
    correct for the common case where the config dir sits directly inside the
    project root (e.g. ``{project}/.orc/`` or ``{project}/orc/``).

    Pass ``repo_root=Path.cwd()`` explicitly when the config dir is nested
    deeper (e.g. ``{project}/src/.orc/`` with ``--config-dir src``).
    """
    global AGENTS_DIR, WORK_DIR, BOARD_FILE, ROLES_DIR, REPO_ROOT, ENV_FILE
    global DEV_WORKTREE, _worktree_sibling
    AGENTS_DIR = agents_dir
    REPO_ROOT = (repo_root or agents_dir.parent).resolve()
    WORK_DIR = agents_dir / "work"
    BOARD_FILE = WORK_DIR / "board.yaml"
    ROLES_DIR = agents_dir / "roles"
    ENV_FILE = REPO_ROOT / ".env"
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

# Placeholder values from .env.example that have not been filled in
_PLACEHOLDERS = {
    "your-bot-token-here",
    "your-chat-id-here",
    "your-gh-token-here",
    "your-anthropic-api-key-here",
    "",
}


def validate_env() -> list[str]:
    """Check that all required .env variables are present and not placeholders.

    Returns a (possibly empty) list of human-readable error strings.
    """
    errors: list[str] = []

    if not ENV_FILE.exists():
        errors.append(
            f".env not found at {ENV_FILE}. Copy .env.example to .env and fill in your credentials."
        )
        return errors  # no point checking individual vars

    def check(var: str, hint: str) -> None:
        val = os.environ.get(var, "").strip()
        if not val or val in _PLACEHOLDERS:
            errors.append(f"{var} is not set. {hint}")

    check("COLONY_TELEGRAM_TOKEN", "Get a bot token from @BotFather on Telegram.")
    check("COLONY_TELEGRAM_CHAT_ID", "Add the bot to a chat and get the numeric chat ID.")

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
        # copilot (or unset): GH_TOKEN → apps.json → gh auth token
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


# ---------------------------------------------------------------------------
# Dev worktree management
# ---------------------------------------------------------------------------


def _ensure_dev_worktree() -> Path:
    """Ensure the ``dev`` branch and its worktree exist.

    - Creates the ``dev`` branch (from HEAD) if it does not exist.
    - Creates a git worktree at ``DEV_WORKTREE`` if it does not exist.
    - Returns the path to the dev worktree.
    """
    # Create dev branch if absent
    existing = subprocess.run(
        ["git", "branch", "--list", WORK_DEV_BRANCH],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if not existing.stdout.strip():
        subprocess.run(
            ["git", "branch", WORK_DEV_BRANCH],
            cwd=REPO_ROOT,
            check=True,
        )

    # Create worktree if absent
    if not DEV_WORKTREE.exists():
        # Prune stale registrations so `add` doesn't fail on a missing-but-registered path.
        subprocess.run(["git", "worktree", "prune"], cwd=REPO_ROOT, check=True)
        subprocess.run(
            ["git", "worktree", "add", str(DEV_WORKTREE), WORK_DEV_BRANCH],
            cwd=REPO_ROOT,
            check=True,
        )

    return DEV_WORKTREE


# ---------------------------------------------------------------------------
# Feature worktree management
# ---------------------------------------------------------------------------


def _feature_branch(task_name: str) -> str:
    """Return the feature branch name for *task_name*.

    Convention: ``feat/<task-stem>``  e.g. ``feat/0003-resource-type-enum``
    """
    return f"feat/{Path(task_name).stem}"


def _feature_worktree_path(task_name: str) -> Path:
    """Return the expected filesystem path of the feature worktree.

    Placed next to the dev worktree:
    ``{DEV_WORKTREE.parent}/{repo-name}-feat-{task-stem}``
    """
    slug = _feature_branch(task_name).replace("/", "-")  # feat-0003-resource-type-enum
    return DEV_WORKTREE.parent / f"{REPO_ROOT.name}-{slug}"


def _ensure_feature_worktree(task_name: str) -> Path:
    """Ensure a feature branch and linked worktree exist for *task_name*.

    - Creates ``feat/<task-stem>`` from ``main`` if it does not exist.
    - Creates the worktree if the directory does not exist.
    - Returns the worktree path.
    """
    branch = _feature_branch(task_name)
    wt_path = _feature_worktree_path(task_name)

    existing = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if not existing.stdout.strip():
        subprocess.run(["git", "branch", branch, "main"], cwd=REPO_ROOT, check=True)

    if not wt_path.exists():
        # Prune stale registrations so `add` doesn't fail on a missing-but-registered path.
        subprocess.run(["git", "worktree", "prune"], cwd=REPO_ROOT, check=True)
        subprocess.run(
            ["git", "worktree", "add", str(wt_path), branch],
            cwd=REPO_ROOT,
            check=True,
        )

    return wt_path


def _close_task_on_board(task_name: str, dev_wt: Path, commit_tag: str = "pending") -> None:
    """Move *task_name* from ``open`` to ``done`` in board.yaml and delete its .md file.

    Writes directly to the dev-worktree copy so the change lands in the same
    commit as the merge.  Pass *commit_tag* after the merge SHA is known.
    """
    from datetime import UTC, datetime

    try:
        config_rel = AGENTS_DIR.relative_to(REPO_ROOT)
    except ValueError:
        config_rel = Path(AGENTS_DIR.name)
    board_path = dev_wt / config_rel / "work" / "board.yaml"
    if not board_path.exists():
        logger.warning("board.yaml not found in dev worktree, skipping board update")
        return

    board = yaml.safe_load(board_path.read_text()) or {}
    board.setdefault("open", [])
    board.setdefault("done", [])

    # Remove from open
    board["open"] = [
        t for t in board["open"] if (t["name"] if isinstance(t, dict) else str(t)) != task_name
    ]

    # Add to done
    board["done"].append(
        {
            "name": task_name,
            "commit-tag": commit_tag,
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )

    board_path.write_text(yaml.dump(board, default_flow_style=False, allow_unicode=True))

    # Delete the task .md file if present
    task_md = dev_wt / config_rel / "work" / task_name
    if task_md.exists():
        task_md.unlink()
        logger.info("deleted task file", path=str(task_md))


def _merge_feature_into_dev(task_name: str) -> None:
    """Merge the feature branch into dev, close the task in board.yaml, and clean up.

    Sequence:
    1. Merge feat/<stem> into dev with ``--no-ff``.
    2. Update board.yaml: move task from open → done, record commit hash.
    3. Commit the board change to dev.
    4. Remove the feature worktree and delete the feature branch.
    """
    branch = _feature_branch(task_name)
    wt_path = _feature_worktree_path(task_name)
    dev_wt = _ensure_dev_worktree()

    logger.info("merging feature into dev", feature_branch=branch, dev_branch=WORK_DEV_BRANCH)
    subprocess.run(["git", "checkout", WORK_DEV_BRANCH], cwd=dev_wt, check=True)
    subprocess.run(
        ["git", "merge", "--no-ff", branch, "-m", f"Merge {branch} into {WORK_DEV_BRANCH}"],
        cwd=dev_wt,
        check=True,
    )

    # Capture the merge commit SHA to tag the board entry
    merge_sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=dev_wt,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    # Update board.yaml and commit to dev
    _close_task_on_board(task_name, dev_wt, commit_tag=merge_sha)
    board_path = dev_wt / "orc" / "work" / "board.yaml"
    if board_path.exists():
        subprocess.run(["git", "add", "orc/work/"], cwd=dev_wt, check=True)
        subprocess.run(
            ["git", "commit", "-m", f"chore(orc): close task {Path(task_name).stem}"],
            cwd=dev_wt,
            check=True,
        )
        logger.info("board updated and committed", task=task_name, commit_tag=merge_sha)

    if wt_path.exists():
        logger.info("removing feature worktree", path=str(wt_path))
        subprocess.run(
            ["git", "worktree", "remove", str(wt_path), "--force"],
            cwd=REPO_ROOT,
            check=True,
        )

    logger.info("deleting feature branch", branch=branch)
    subprocess.run(["git", "branch", "-d", branch], cwd=REPO_ROOT)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

KNOWN_AGENTS = tg.KNOWN_ROLES

# Regex for [orc](resolved) messages — orc is not in KNOWN_AGENTS but we need
# to detect its resolution markers when scanning for unresolved blocks.
_ORC_RESOLVED_RE = _re.compile(r"^\[orc\]\(resolved\)\s+\S+:\s+.*$")


def _dev_board_file() -> Path:
    """Return the board.yaml that is currently authoritative.

    Prefers the dev-worktree copy (where the planner writes) when the worktree
    exists, falling back to the main-repo copy.
    """
    # Mirror the config dir path relative to REPO_ROOT into DEV_WORKTREE.
    try:
        rel = AGENTS_DIR.relative_to(REPO_ROOT)
    except ValueError:
        rel = Path(AGENTS_DIR.name)
    candidate = DEV_WORKTREE / rel / "work" / "board.yaml"
    return candidate if candidate.exists() else BOARD_FILE


def _read_board() -> dict:
    """Parse board.yaml and return its full structure (empty dict on error)."""
    path = _dev_board_file()
    if not path.exists():
        return {"counter": 0, "open": [], "done": []}
    try:
        data = yaml.safe_load(path.read_text()) or {}
        data.setdefault("open", [])
        data.setdefault("done", [])
        return data
    except Exception:
        return {"counter": 0, "open": [], "done": []}


def _write_board(board: dict) -> None:
    """Persist *board* to the authoritative board.yaml path."""
    path = _dev_board_file()
    path.write_text(yaml.dump(board, default_flow_style=False, allow_unicode=True))


def get_open_tasks() -> list[dict]:
    """Return the list of open task dicts from board.yaml.

    Each element is guaranteed to be a ``dict`` with at least a ``"name"`` key.
    """
    board = _read_board()
    result = []
    for t in board.get("open", []):
        if isinstance(t, dict):
            result.append(t)
        else:
            result.append({"name": str(t)})
    return result


def assign_task(task_name: str, agent_id: str) -> None:
    """Write ``assigned_to: {agent_id}`` for *task_name* in board.yaml."""
    board = _read_board()
    for t in board.get("open", []):
        if isinstance(t, dict) and t.get("name") == task_name:
            t["assigned_to"] = agent_id
            _write_board(board)
            logger.debug("task assigned", task=task_name, agent_id=agent_id)
            return
    logger.warning("assign_task: task not found in board", task=task_name)


def unassign_task(task_name: str) -> None:
    """Clear the ``assigned_to`` field for *task_name* in board.yaml."""
    board = _read_board()
    changed = False
    for t in board.get("open", []):
        if isinstance(t, dict) and t.get("name") == task_name:
            t.pop("assigned_to", None)
            changed = True
            break
    if changed:
        _write_board(board)
        logger.debug("task unassigned", task=task_name)


def clear_all_assignments() -> None:
    """Clear all ``assigned_to`` fields — called on startup for crash recovery.

    After an unclean shutdown running agents may still appear as assigned in
    board.yaml even though their processes are gone.  Clearing on restart lets
    the dispatcher re-derive state from git and re-assign cleanly.
    """
    board = _read_board()
    changed = False
    for t in board.get("open", []):
        if isinstance(t, dict) and t.pop("assigned_to", None) is not None:
            changed = True
    if changed:
        _write_board(board)
        logger.info("cleared stale task assignments on startup")


def _active_task_name() -> str | None:
    """Return the file name of the first open task, or None if the board is empty."""
    board = _read_board()
    open_tasks = board.get("open", [])
    if not open_tasks:
        return None
    first = open_tasks[0]
    return first["name"] if isinstance(first, dict) else str(first)


def has_open_work() -> bool:
    """Return ``True`` if board.yaml has at least one task in the open list."""
    return _active_task_name() is not None


def _read_work() -> str:
    """Return a human-readable summary of the kanban board + open task files."""
    parts: list[str] = []

    board_path = _dev_board_file()
    if board_path.exists():
        parts.append(f"### orc/work/board.yaml\n\n```yaml\n{board_path.read_text().strip()}\n```")

    work_dir = board_path.parent if board_path.exists() else WORK_DIR
    for task_file in sorted(work_dir.glob("*.md")):
        parts.append(f"### {task_file.name}\n\n{task_file.read_text()}")

    return "\n\n".join(parts) if parts else "_No active work._"


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

# Sentinel return values — handled by the dispatcher before any agent dispatch.
_QA_PASSED = _disp.QA_PASSED  # last commit is qa(passed): → merge then re-derive
_CLOSE_BOARD = _disp.CLOSE_BOARD  # branch gone but board not updated → close board


def _feature_has_commits_ahead_of_main(branch: str) -> bool:
    """Return True if *branch* has at least one commit not in main."""
    result = subprocess.run(
        ["git", "log", "main.." + branch, "--oneline"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _feature_merged_into_dev(branch: str) -> bool:
    """Return True if *branch* has been merged into dev (is an ancestor)."""
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", branch, WORK_DEV_BRANCH],
        cwd=REPO_ROOT,
    )
    return result.returncode == 0


def _feature_branch_exists(branch: str) -> bool:
    """Return True if *branch* exists locally."""
    result = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _last_feature_commit_message(branch: str) -> str | None:
    """Return the subject line of the most recent commit on *branch*, or None."""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%s", branch],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or None


def _derive_task_state(task_name: str) -> tuple[str, str]:
    """Inspect the git tree for *task_name* and return ``(token, reason)``.

    This is the parameterised form of the former ``_derive_state_from_git()``
    so the dispatcher can call it for **any** open task (enabling parallel
    dispatch of multiple coders on different tasks).

    *token* is one of:

    - ``"planner"`` / ``"coder"`` / ``"qa"``  — dispatch that role.
    - ``_QA_PASSED``   — QA committed a passed result; merge before re-deriving.
    - ``_CLOSE_BOARD`` — crash-recovery: branch merged but board not updated.
    """
    branch = _feature_branch(task_name)

    if not _feature_branch_exists(branch):
        if _feature_merged_into_dev(branch):
            return _CLOSE_BOARD, f"branch {branch!r} merged but board not updated"
        return "coder", f"feature branch {branch!r} does not exist yet"

    if not _feature_has_commits_ahead_of_main(branch):
        return "coder", f"feature branch {branch!r} has no commits ahead of main"

    last_msg = _last_feature_commit_message(branch)
    if last_msg and last_msg.startswith("qa(passed)"):
        return _QA_PASSED, f"qa passed on {branch!r} — ready to merge"
    if last_msg and last_msg.startswith("qa("):
        return "coder", f"qa reviewed {branch!r} with issues: {last_msg!r}"

    return "qa", f"coder has commits on {branch!r}, awaiting review"


def _derive_state_from_git() -> tuple[str, str]:
    """Derive the next-agent token from git for the currently active task.

    Wraps :func:`_derive_task_state` for the single-task (sequential) path.
    Returns ``("planner", "no open tasks on board")`` when the board is empty.
    """
    active_task = _active_task_name()
    if not active_task:
        return "planner", "no open tasks on board"
    return _derive_task_state(active_task)


def _has_unresolved_block(
    messages: list[dict],
) -> tuple[str, str] | tuple[None, None]:
    """Scan *messages* newest-to-oldest for an unresolved blocked/soft-blocked state.

    A block is considered resolved when a later ``[orc](resolved)`` message or a
    later non-boot terminal message from any known agent appears after it.

    Returns ``(agent_name, state)`` if an unresolved block is found, else
    ``(None, None)``.
    """
    blocked_states = {"blocked", "soft-blocked"}

    for msg in reversed(messages):
        text = msg.get("text", "").strip()

        # An orc resolved marker closes any prior block
        if _ORC_RESOLVED_RE.match(text):
            return None, None

        m = tg._MSG_RE.match(text)
        if not m:
            continue
        name, state = m.group(1), m.group(2)
        role, _ = tg.parse_agent_id(name)
        if role is None:
            continue
        if state in tg.INFORMATIONAL_STATES:
            continue

        if state in blocked_states:
            return name, state

        # Any non-boot terminal state from a known agent also closes prior blocks
        return None, None

    return None, None


def _post_resolved(blocked_agent: str, blocked_state: str, resolver_agent: str) -> None:
    """Post an ``[orc](resolved)`` message to Telegram to close a blocked state."""
    body = f"{blocked_agent}({blocked_state}) addressed by {resolver_agent} invocation."
    tg.send_message(tg.format_agent_message("orc", "resolved", body))
    logger.info(
        "posted resolved message",
        blocked_agent=blocked_agent,
        blocked_state=blocked_state,
        resolver=resolver_agent,
    )


def _post_boot_message(agent_id: str, body: str) -> None:
    """Send ``[{agent_id}](boot) …`` to Telegram."""
    tg.send_message(tg.format_agent_message(agent_id, "boot", body))


def _do_close_board(task_name: str) -> None:
    """Crash-recovery: close *task_name* on board and commit via dev worktree."""
    dev_wt = _ensure_dev_worktree()
    logger.warning("crash recovery: closing board for merged branch", task=task_name)
    typer.echo(f"\n⟳ Crash recovery: closing board entry for {task_name}…")
    _close_task_on_board(task_name, dev_wt)
    board_path = dev_wt / "orc" / "work" / "board.yaml"
    if board_path.exists():
        subprocess.run(["git", "add", "orc/work/"], cwd=dev_wt, check=True)
        subprocess.run(
            [
                "git",
                "commit",
                "-m",
                f"chore(orc): close task {Path(task_name).stem} (recovery)",
            ],
            cwd=dev_wt,
            check=True,
        )


def _make_context_builder(
    squad_cfg: SquadConfig,
) -> Callable[[str, str, list[dict], Path | None], tuple[str, str]]:
    """Return a ``build_context`` callback that sources models from *squad_cfg*."""

    def _build(
        role: str,
        agent_id: str,
        messages: list[dict],
        worktree: Path | None,
    ) -> tuple[str, str]:
        return build_agent_context(
            role,
            messages,
            worktree=worktree,
            agent_id=agent_id,
            model=squad_cfg.model(role),
        )

    return _build


def determine_next_agent(messages: list[dict]) -> tuple[str | None, str]:
    """Return ``(next_agent, reason)`` for the current workflow state.

    Priority:
    1. Unresolved ``blocked`` → ``None`` (needs human intervention).
    2. Unresolved ``soft-blocked`` → ``"planner"`` (needs spec clarification).
    3. Git-derived state (primary signal) — see ``_derive_state_from_git``.
       May return action sentinels ``_QA_PASSED`` or ``_CLOSE_BOARD``; the
       ``run()`` loop handles these before dispatching any agent.

    Returns ``None`` as the agent only for hard-blocked states.
    """
    blocked_agent, blocked_state = _has_unresolved_block(messages)
    if blocked_agent:
        if blocked_state == "soft-blocked":
            reason = f"{blocked_agent}(soft-blocked) — needs planner clarification"
            logger.info("unresolved soft-block, routing to planner", **{"from": blocked_agent})
            return "planner", reason
        reason = f"{blocked_agent}(blocked) — needs human intervention"
        logger.warning("unresolved hard block, stopping", agent=blocked_agent)
        return None, reason

    agent, reason = _derive_state_from_git()
    logger.info("git-derived state", next_agent=agent, reason=reason)
    return agent, reason


# ---------------------------------------------------------------------------
# Context building
# ---------------------------------------------------------------------------


def _read_adrs() -> str:
    adr_dir = REPO_ROOT / "docs" / "adr"
    parts: list[str] = []
    if not adr_dir.exists():
        return "_No ADRs found._"
    for adr_file in sorted(adr_dir.glob("*.md")):
        if adr_file.name == "README.md":
            continue
        parts.append(f"### {adr_file.name}\n\n{adr_file.read_text()}")
    return "\n\n---\n\n".join(parts) if parts else "_No ADRs found._"


_DEFAULT_MODEL = "claude-sonnet-4.6"


def _parse_role_file(agent_name: str) -> str:
    """Read a role file and return its content.

    Searches project ROLES_DIR first, then falls back to the package's
    bundled roles directory.  If the file starts with a YAML front-matter
    block (``---`` … ``---``), the block is stripped from the returned content.
    """
    role_file = ROLES_DIR / f"{agent_name}.md"
    if not role_file.exists():
        role_file = _PACKAGE_ROLES_DIR / f"{agent_name}.md"
    if not role_file.exists():
        return f"You are the {agent_name} agent."

    raw = role_file.read_text()

    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            raw = raw[end + 4 :].lstrip("\n")

    return raw


def build_agent_context(
    agent_name: str,
    messages: list[dict],
    extra: str = "",
    worktree: Path | None = None,
    agent_id: str | None = None,
    model: str | None = None,
) -> tuple[str, str]:
    """Return ``(model, context)`` for the given agent.

    The *model* parameter specifies which AI model to use.  When provided it
    takes precedence; when omitted it falls back to ``_DEFAULT_MODEL``.  The
    recommended source for the model is the squad profile (``SquadConfig.model``).

    Combines the role content with all shared context documents (README,
    CONTRIBUTING, ADRs, Telegram chat history, kanban board).

    *worktree* is the directory where the agent will run.  When omitted it
    defaults to the dev worktree.  Pass *extra* to prepend additional context
    (e.g. merge-conflict details) before the shared sections.

    *agent_id* — the unique agent identifier (e.g. ``"coder-1"``).  Injected
    into the context so the agent knows its own ID for Telegram messages.
    """
    resolved_model = model or _DEFAULT_MODEL
    role = _parse_role_file(agent_name)

    dev_worktree = _ensure_dev_worktree()
    orc_readme_path = AGENTS_DIR / "README.md"
    orc_readme = orc_readme_path.read_text() if orc_readme_path.exists() else ""
    readme_path = REPO_ROOT / "README.md"
    readme = readme_path.read_text() if readme_path.exists() else ""
    contributing_path = REPO_ROOT / "CONTRIBUTING.md"
    contributing = contributing_path.read_text() if contributing_path.exists() else ""
    adrs = _read_adrs()
    chat = tg.messages_to_text(messages)
    plans = _read_work()

    active_task = _active_task_name()
    feature_branch = _feature_branch(active_task) if active_task else None
    feature_wt = _feature_worktree_path(active_task) if active_task else None

    id_line = (
        f"\n**Your agent ID**: `{agent_id}` — use this ID in all Telegram messages.\n"
        if agent_id
        else ""
    )

    if agent_name == "coder" and feature_branch:
        git_info = (
            f"Your branch: `{feature_branch}` (cut from `main`)\n"
            f"Your worktree: `{feature_wt}` — all edits and git commands go here\n"
            f"Dev branch: `{WORK_DEV_BRANCH}` (managed by planner and QA — do not touch)\n"
            f"Main worktree: `{REPO_ROOT}` (human's workspace — do not touch)\n\n"
            f"Work exclusively in your feature worktree (`{feature_wt}`). "
            f"Commit to `{feature_branch}` only. "
            f"The orchestrator will merge your branch into `{WORK_DEV_BRANCH}` after QA passes."
        )
    elif agent_name == "qa" and feature_branch:
        git_info = (
            f"Branch to review: `{feature_branch}`\n"
            f"Feature worktree: `{feature_wt}`\n"
            f"Dev branch: `{WORK_DEV_BRANCH}`\n"
            f"Dev worktree: `{dev_worktree}`\n"
            f"Main worktree: `{REPO_ROOT}` (human's workspace — do not touch)\n\n"
            f"Review `{feature_branch}` against `{WORK_DEV_BRANCH}` "
            f"(e.g. `git diff {WORK_DEV_BRANCH}...{feature_branch}`).\n"
            f"Run in the dev worktree (`{dev_worktree}`). "
            f"**Do NOT merge** — the orchestrator merges after you signal `passed`."
        )
    else:
        git_info = (
            f"Dev branch: `{WORK_DEV_BRANCH}`\n"
            f"Dev worktree path: `{dev_worktree}`\n"
            f"Main worktree path: `{REPO_ROOT}` (human's workspace — do not touch)\n\n"
            f"All file edits and git commands must be performed inside the dev "
            f"worktree (`{dev_worktree}`)."
        )
        if feature_branch:
            git_info += f"\nActive feature branch: `{feature_branch}` (coder's branch)"

    extra_section = f"## Current task\n\n{extra}\n\n" if extra else ""

    context = (
        f"{role}\n"
        f"{id_line}\n"
        "---\n\n"
        f"{extra_section}"
        "## Shared context\n\n"
        f"### Git workflow\n\n{git_info}\n\n"
        f"### Orc workflow documentation (orc/README.md)\n\n{orc_readme}\n\n"
        f"### README\n\n{readme}\n\n"
        f"### CONTRIBUTING\n\n{contributing}\n\n"
        f"### Architecture Decision Records\n\n{adrs}\n\n"
        f"### Chat history (Telegram)\n\n{chat}\n\n"
        f"### Kanban board (orc/work/)\n\n{plans}\n"
    )
    return resolved_model, context


# ---------------------------------------------------------------------------
# Blocked-state recovery
# ---------------------------------------------------------------------------

_BLOCKED_TIMEOUT = 3600.0  # seconds before giving up on a human reply


def wait_for_human_reply(
    messages_snapshot: list[dict],
    *,
    initial_delay: float = 5.0,
    backoff_factor: float = 2.0,
    max_delay: float = 300.0,
    timeout: float = _BLOCKED_TIMEOUT,
) -> str:
    """Poll Telegram until a new human message appears after *messages_snapshot*.

    Uses exponential backoff between polls (initial_delay → max_delay seconds).
    Raises ``TimeoutError`` if no human reply arrives within *timeout* seconds.
    Returns the text of the first new human message found.
    """
    seen = frozenset((m.get("date", 0), m.get("text", "")) for m in messages_snapshot)
    delay = initial_delay
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"No human reply received within {timeout:.0f}s.")
        actual_delay = min(delay, remaining)
        print(f"  … waiting {actual_delay:.0f}s for your reply in Telegram…")
        time.sleep(actual_delay)
        for msg in tg.get_messages():
            key = (msg.get("date", 0), msg.get("text", ""))
            if key not in seen and not tg.is_agent_message(msg.get("text", "")):
                return msg.get("text", "")
        delay = min(delay * backoff_factor, max_delay)


# ---------------------------------------------------------------------------
# Invocation
# ---------------------------------------------------------------------------


def _boot_message_body() -> str:
    """Build the body text for a (boot) message listing open work items."""
    board = _read_board()
    open_tasks = board.get("open", [])
    if not open_tasks:
        return "no open tasks on board."
    names = [(t["name"] if isinstance(t, dict) else str(t)) for t in open_tasks]
    paths = ", ".join(f"work/{n}" for n in names)
    return f"picking up {paths}."


def invoke_agent(agent_name: str, context: str, model: str, worktree: Path | None = None) -> int:
    """Invoke the configured AI CLI with the agent's full context prompt.

    The backend (``copilot`` or ``claude``) is selected by ``COLONY_AI_CLI``
    in ``.env``.  *model* is forwarded to the backend where supported.
    *worktree* sets the working directory for the agent process; defaults to
    the dev worktree.  Returns the subprocess exit code.
    """
    cwd = worktree or _ensure_dev_worktree()
    return inv.invoke(context, cwd=cwd, model=model)


# ---------------------------------------------------------------------------
# Merge helpers
# ---------------------------------------------------------------------------


def _rebase_in_progress(worktree: Path) -> bool:
    """Return True if a rebase is currently paused in *worktree*."""
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    git_dir = Path(result.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = worktree / git_dir
    return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()


def _complete_merge(worktree: Path) -> None:
    """Fast-forward merge dev into main, then switch back to dev."""
    subprocess.run(["git", "checkout", "main"], cwd=worktree, check=True)
    subprocess.run(["git", "merge", "--ff-only", WORK_DEV_BRANCH], cwd=worktree, check=True)
    subprocess.run(["git", "checkout", WORK_DEV_BRANCH], cwd=worktree, check=True)


def _conflict_status(worktree: Path) -> str:
    """Return the output of ``git status --short`` in *worktree*."""
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="orc",
    help="orc multi-agent orchestrator.",
    no_args_is_help=True,
)


@app.callback()
def _app_entry(
    config_dir: Annotated[
        Path | None,
        typer.Option(
            "--config-dir",
            help=(
                "Base directory to search for the orc configuration folder. "
                "orc looks for <config-dir>/.orc/ then <config-dir>/orc/. "
                "Defaults to the current working directory."
            ),
            show_default=False,
        ),
    ] = None,
) -> None:
    """Bootstrap observability and resolve the config directory."""
    _obs.setup()
    if config_dir is not None:
        found = _find_config_dir(base=config_dir)
        if found is None:
            typer.echo(
                f"✗ No orc config directory found in '{config_dir}'.\n"
                f"  Expected '{config_dir}/.orc/' or '{config_dir}/orc/'.\n"
                "  Run 'orc bootstrap' to create one.",
                err=True,
            )
            raise typer.Exit(code=1)
        _init_paths(found, repo_root=Path.cwd())


def _check_env_or_exit() -> None:
    if not AGENTS_DIR.is_dir():
        typer.echo(
            f"✗ orc configuration directory not found.\n"
            f"  Searched: {AGENTS_DIR.parent}/.orc/  and  {AGENTS_DIR.parent}/orc/\n"
            "  Run 'orc bootstrap' to create one, or pass --config-dir <base> to "
            "point to an existing configuration.",
            err=True,
        )
        raise typer.Exit(code=1)
    errors = validate_env()
    if errors:
        typer.echo("✗ Configuration errors — fix .env before running:\n", err=True)
        for err in errors:
            typer.echo(f"  • {err}", err=True)
        raise typer.Exit(code=1)


def _dev_ahead_of_main() -> int:
    """Return the number of commits dev is ahead of main (0 if even or behind)."""
    result = subprocess.run(
        ["git", "rev-list", "--count", "main..dev"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    try:
        return int(result.stdout.strip())
    except (ValueError, AttributeError):
        return 0


def _dev_log_since_main() -> list[str]:
    """Return one-line summaries of commits on dev not yet in main."""
    result = subprocess.run(
        ["git", "log", "--oneline", "--no-decorate", "main..dev"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        return []
    return result.stdout.strip().splitlines()


@app.command()
def status() -> None:
    """Print current workflow state without running any agent."""
    messages = tg.get_messages()
    blocked_agent, blocked_state = _has_unresolved_block(messages)
    next_agent, reason = determine_next_agent(messages)
    # Translate action sentinels into human-readable labels for display
    display_agent = next_agent
    if next_agent == _QA_PASSED:
        display_agent = "(merge pending)"
    elif next_agent == _CLOSE_BOARD:
        display_agent = "(board close pending)"
    typer.echo(f"Open work  : {'yes' if has_open_work() else 'none'}")
    if blocked_agent:
        typer.echo(f"Blocked    : {blocked_agent}({blocked_state})")
    typer.echo(f"Next agent : {display_agent or '(none – workflow blocked)'} — {reason}")

    # --- Board summary ---------------------------------------------------
    board = _read_board()
    open_tasks = board.get("open", [])
    done_tasks = board.get("done", [])

    if open_tasks:
        typer.echo("\nPending tasks:")
        for task in open_tasks:
            name = task["name"] if isinstance(task, dict) else str(task)
            branch = _feature_branch(name)
            if _feature_branch_exists(branch):
                last = _last_feature_commit_message(branch) or ""
                typer.echo(f"  • {name}  ({branch})  last: {last}")
            else:
                typer.echo(f"  • {name}  (no branch yet)")

    # --- dev vs main -----------------------------------------------------
    ahead = _dev_ahead_of_main()
    if ahead:
        typer.echo(f"\ndev is {ahead} commit{'s' if ahead != 1 else ''} ahead of main")
        log_lines = _dev_log_since_main()
        for line in log_lines:
            typer.echo(f"  {line}")
        typer.echo("\nRun `orc merge` to fast-forward main.")
    else:
        typer.echo("\nmain is up to date with dev.")

    # --- Done log --------------------------------------------------------
    if done_tasks:
        typer.echo("\nCompleted tasks:")
        for task in done_tasks:
            name = task.get("name", "?") if isinstance(task, dict) else str(task)
            tag = task.get("commit-tag", "?") if isinstance(task, dict) else "?"
            ts = task.get("timestamp", "") if isinstance(task, dict) else ""
            ts_str = f"  {ts}" if ts else ""
            typer.echo(f"  ✓ {name}  ({tag}){ts_str}")


def _rebase_dev_on_main(messages: list, squad_cfg: SquadConfig | None = None) -> None:
    """Rebase dev on top of main so every session starts with the latest instructions.

    If the rebase is clean, the worktree is left on the updated dev branch
    (no merge into main — the full merge is done explicitly via ``orc merge``).
    On conflict, the coder agent is invoked to resolve them; the orchestrator
    then re-applies the rebase so the branch is clean before the session starts.
    """
    dev_worktree = _ensure_dev_worktree()

    result = subprocess.run(["git", "rebase", "main"], cwd=dev_worktree)
    if result.returncode == 0:
        typer.echo("✓ dev rebased on main.")
        return

    status_output = _conflict_status(dev_worktree)
    typer.echo(f"⚠ Startup rebase conflict:\n{status_output}\nDelegating to coder agent…")

    conflict_extra = (
        "## Startup rebase conflict — your task\n\n"
        f"A `git rebase main` of the `{WORK_DEV_BRANCH}` branch was attempted at session "
        "start and stopped with conflicts.  The rebase is currently paused in the dev "
        "worktree.\n\n"
        f"Conflicting files (from `git status --short`):\n```\n{status_output}\n```\n\n"
        "**What you must do:**\n"
        "1. Open each conflicting file, resolve the conflict markers (`<<<<<<<`, "
        "`=======`, `>>>>>>>`).\n"
        "2. `git add <resolved-file>` for each resolved file.\n"
        "3. `git rebase --continue` (repeat steps 1–3 if git stops again).\n"
        "4. Do NOT `git rebase --abort`. Finish the rebase.\n"
        "5. Exit when the rebase is complete.\n"
    )

    coder_model = squad_cfg.model("coder") if squad_cfg is not None else _DEFAULT_MODEL
    model, context = build_agent_context("coder", messages, extra=conflict_extra, model=coder_model)
    rc = invoke_agent("coder", context, model)

    if rc != 0:
        logger.error("coder agent failed to resolve startup rebase", exit_code=rc)
        typer.echo(f"✗ Coder agent exited with code {rc} while resolving startup rebase.")
        raise typer.Exit(code=rc)

    if _rebase_in_progress(dev_worktree):
        logger.error("rebase still in progress after coder exited")
        typer.echo("✗ Rebase still in progress after agent exit. Manual intervention needed.")
        raise typer.Exit(code=1)

    logger.info("dev rebased on main after conflict resolution by coder")
    typer.echo("✓ dev rebased on main (conflicts resolved by coder).")


@app.command()
def squads() -> None:
    """List available squad profiles and their composition."""
    _obs.setup()
    profiles = load_all_squads(agents_dir=AGENTS_DIR)
    if not profiles:
        typer.echo("No squad profiles found.")
        return

    typer.echo("\nAvailable squad profiles:\n")
    for cfg in profiles:
        coder_label = f"{cfg.coder} coder{'s' if cfg.coder != 1 else ''}"
        qa_label = f"{cfg.qa} QA"
        composition = f"1 planner · {coder_label} · {qa_label} · {cfg.timeout_minutes} min"
        typer.echo(f"  {cfg.name:<12}  {composition}")
        if cfg.description:
            for line in cfg.description.strip().splitlines():
                typer.echo(f"               {line}")
        typer.echo("")


@app.command()
def merge() -> None:
    """Rebase dev on top of main and fast-forward merge dev into main.

    If the rebase produces conflicts the coder agent is invoked to resolve them.
    Once the agent exits the merge is completed automatically.
    """
    _check_env_or_exit()
    messages = tg.get_messages()
    _rebase_dev_on_main(messages)
    dev_worktree = _ensure_dev_worktree()
    _complete_merge(dev_worktree)
    typer.echo("✓ dev merged into main.")


@app.command()
def run(
    maxloops: Annotated[
        int,
        typer.Option(
            help=(
                "Maximum agent invocations before stopping. "
                "0 = run until the workflow completes or blocks."
            ),
        ),
    ] = 1,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the agent context/prompt without invoking."),
    ] = False,
    squad: Annotated[
        str,
        typer.Option(
            "--squad",
            help="Squad profile name (file in orc/squads/).  Default: 'default'.",
        ),
    ] = "default",
) -> None:
    """Run the next agent(s) in the workflow."""
    _check_env_or_exit()

    squad_cfg = load_squad(squad, agents_dir=AGENTS_DIR)
    logger.info(
        "orc run starting",
        maxloops=maxloops,
        dry_run=dry_run,
        squad=squad,
        coders=squad_cfg.coder,
        qa=squad_cfg.qa,
    )
    typer.echo("⟳ Syncing dev on main…")
    messages = tg.get_messages()
    _rebase_dev_on_main(messages, squad_cfg)

    clear_all_assignments()

    callbacks = _disp.DispatchCallbacks(
        derive_task_state=_derive_task_state,
        get_open_tasks=get_open_tasks,
        assign_task=assign_task,
        unassign_task=unassign_task,
        ensure_feature_worktree=_ensure_feature_worktree,
        ensure_dev_worktree=_ensure_dev_worktree,
        merge_feature=_merge_feature_into_dev,
        do_close_board=_do_close_board,
        get_messages=tg.get_messages,
        has_unresolved_block=_has_unresolved_block,
        wait_for_human_reply=wait_for_human_reply,
        post_boot_message=_post_boot_message,
        post_resolved=_post_resolved,
        boot_message_body=_boot_message_body,
        build_context=_make_context_builder(squad_cfg),
        spawn_fn=inv.spawn,
    )

    try:
        dispatcher = _disp.Dispatcher(squad_cfg, callbacks, dry_run=dry_run)
        dispatcher.run(maxloops=maxloops)
    except Exception:
        logger.exception("orc run loop crashed")
        raise


_JUSTFILE_CONTENT = """\
# orc agent orchestrator recipes.
#
# Usage from root: add this to your root justfile:
#
#   mod orc 'orc/justfile'
#
# Then: just orc run, just orc status, just orc merge

repo_root := justfile_directory() / ".."

# List available orc recipes
default:
    @just --list --justfile {{{{source_file()}}}}

# Run the next agent(s) in the workflow
# Examples:
#   just orc run                          # default squad, 1 invocation
#   just orc run --maxloops 0             # run until complete
#   just orc run --squad broad            # load orc/squads/broad.yaml
#   just orc run --dry-run                # print context without invoking
run *args:
    cd {{{{repo_root}}}} && uv run orc run {{{{args}}}}

# Print current workflow state without running any agent
status:
    cd {{{{repo_root}}}} && uv run orc status

# Rebase dev on main and fast-forward merge into main
merge:
    cd {{{{repo_root}}}} && uv run orc merge

# List available squad profiles and their composition
squads:
    cd {{{{repo_root}}}} && uv run orc squads
"""

_ENV_EXAMPLE_CONTENT = """\
# AI backend: "copilot" (GitHub Copilot CLI) or "claude" (Anthropic API)
COLONY_AI_CLI=copilot

# Anthropic API key — required only when COLONY_AI_CLI=claude
ANTHROPIC_API_KEY=your-anthropic-key-here

# GitHub personal access token — required only when COLONY_AI_CLI=copilot
GH_TOKEN=your-gh-token-here

# Telegram bot credentials (from @BotFather)
COLONY_TELEGRAM_TOKEN=your-bot-token-here
COLONY_TELEGRAM_CHAT_ID=your-chat-id-here

# Optional: override the orc configuration directory search.
# orc searches $CWD/.orc/ first, then $CWD/orc/, then exits with an error.
# Setting this env var skips the search and uses the specified path directly.
# ORC_DIR=/absolute/path/to/orc
"""

_VISION_README = """\
# Vision

This folder contains vision documents for the project.

Vision documents are the source of truth for _what_ to build. The planner agent
reads them and translates each piece of work into either an ADR (`docs/adr/`) or
a task (`orc/work/`).

## Format

Each vision document is a markdown file describing a feature, system, or
product direction. There is no strict format, but a good vision document
includes:

- **What** – the feature or capability being described
- **Why** – the motivation and value for the user/project
- **Constraints** – things that must be true of the implementation
- **Out of scope** – things explicitly not included

## Getting started

Add `.md` files here describing what you want to build. The planner will pick
them up on the next `orc run`.
"""

_ROLES_README = """\
# Role overrides

Drop `.md` files here to override the bundled agent role prompts for this
project.  Any file placed here takes precedence over the package defaults.

Expected filenames:

- `planner.md` – instructions for the planner agent
- `coder.md`   – instructions for the coder agent
- `qa.md`      – instructions for the QA agent

If a file is absent the bundled template is used unchanged.

To select the AI model for each role, set it in the squad profile
(``orc/squads/*.yaml``) rather than in the role file.
"""

_SQUADS_README = """\
# Squad profiles

Drop `.yaml` files here to define or override squad configurations for this
project.  Project-level profiles take precedence over the package defaults.

## Schema

```yaml
name: broad
description: |
  Wider parallel configuration for larger projects.
composition:
  - role: planner
    count: 1                  # must always be 1
    model: claude-sonnet-4.6
  - role: coder
    count: 4                  # parallel coders
    model: claude-sonnet-4.6
  - role: qa
    count: 2                  # parallel QA reviewers
    model: claude-sonnet-4.6
timeout_minutes: 180
```

Run `orc squads` to list all available profiles.
"""

_BOARD_YAML = """\
# orc kanban board
#
# counter  – next available task ID (integer; format as 4-digit zero-padded
#            string when naming files, e.g. counter=3 → "0003-title.md").
#            The planner increments this every time it creates a new task.
#
# open     – tasks currently being worked on.
# done     – completed tasks.

counter: 1

open: []

done: []
"""


def _write_file(path: Path, content: str, created: list[str], skipped: list[str]) -> None:
    """Write *content* to *path* if it does not exist; record the outcome."""
    if path.exists():
        skipped.append(str(path))
    else:
        path.write_text(content)
        created.append(str(path))


def _copy_file(src: Path, dst: Path, created: list[str], skipped: list[str]) -> None:
    """Copy *src* to *dst* if *dst* does not exist; record the outcome."""
    import shutil

    if dst.exists():
        skipped.append(str(dst))
    else:
        shutil.copy2(src, dst)
        created.append(str(dst))


@app.command()
def bootstrap(
    to: Annotated[
        str,
        typer.Option(
            "--to",
            help="Path (relative to CWD) for the orc configuration directory to create.",
        ),
    ] = ".orc",
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite existing files."),
    ] = False,
) -> None:
    """Scaffold an orc configuration directory in the current project.

    Creates the .orc/ directory structure, copies bundled role templates and
    the default squad profile, and generates a justfile.

    After bootstrapping:

    \\b
    1. Edit .orc/roles/*.md to customise the agent instructions for your project.
    2. Add vision documents to .orc/vision/.
    3. Add 'mod orc \\".orc/justfile\\"' to your root justfile (if you use just).
    4. Copy .env.example to .env and fill in your credentials.
    5. Run: just orc run   (or: orc run)
    """
    _obs.setup()
    project_root = Path.cwd()
    target = (project_root / to).resolve()

    created: list[str] = []
    skipped: list[str] = []

    if force:
        # In force mode, patch _write_file/_copy_file to always overwrite.
        import shutil as _shutil

        def _write(path: Path, content: str, c: list, s: list) -> None:
            path.write_text(content)
            c.append(str(path))

        def _copy(src: Path, dst: Path, c: list, s: list) -> None:
            _shutil.copy2(src, dst)
            c.append(str(dst))

    else:
        _write = _write_file  # type: ignore[assignment]
        _copy = _copy_file  # type: ignore[assignment]

    # ── directories ──────────────────────────────────────────────────────────
    for subdir in ("roles", "squads", "vision", "work"):
        (target / subdir).mkdir(parents=True, exist_ok=True)

    # ── bundled roles ─────────────────────────────────────────────────────────
    for role in ("planner", "coder", "qa"):
        src = _PACKAGE_DIR / "roles" / f"{role}.md"
        _copy(src, target / "roles" / f"{role}.md", created, skipped)

    # ── bundled squads ────────────────────────────────────────────────────────
    _copy(
        _PACKAGE_DIR / "squads" / "default.yaml",
        target / "squads" / "default.yaml",
        created,
        skipped,
    )

    # ── generated files ───────────────────────────────────────────────────────
    _write(target / "roles" / "README.md", _ROLES_README, created, skipped)
    _write(target / "squads" / "README.md", _SQUADS_README, created, skipped)
    _write(target / "vision" / "README.md", _VISION_README, created, skipped)
    _write(target / "work" / "board.yaml", _BOARD_YAML, created, skipped)
    _write(target / "justfile", _JUSTFILE_CONTENT, created, skipped)
    _write(project_root / ".env.example", _ENV_EXAMPLE_CONTENT, created, skipped)

    # ── summary ───────────────────────────────────────────────────────────────
    rel = lambda p: Path(p).relative_to(project_root)  # noqa: E731

    if created:
        typer.echo("\n✓ Created:")
        for f in created:
            typer.echo(f"    {rel(f)}")

    if skipped:
        typer.echo("\n⚠ Skipped (already exists):")
        for f in skipped:
            typer.echo(f"    {rel(f)}")
        typer.echo("  Use --force to overwrite.")

    typer.echo(
        f"""
Next steps
──────────
1. Edit {to}/roles/*.md  — customise agent instructions for your project.
2. Add vision docs to {to}/vision/  — describe what you want to build.
3. Copy .env.example → .env and fill in your credentials.
4. Add to your root justfile:

       mod orc '{to}/justfile'

   Then run:  just orc run

   Or without just:  orc run
"""
    )


if __name__ == "__main__":  # pragma: no cover
    app()
