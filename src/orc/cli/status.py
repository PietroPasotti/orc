"""orc status command."""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Annotated

import structlog
import typer

import orc.board as _board
import orc.config as _cfg
import orc.engine.context as _ctx
import orc.engine.workflow as _wf
import orc.git.core as _git
from orc.cli import app
from orc.engine.dispatcher import QA_PASSED as _QA_PASSED
from orc.engine.state_machine import LastCommit as _LastCommit
from orc.engine.work import Work
from orc.messaging import telegram as tg
from orc.squad import AgentRole, load_squad

logger = structlog.get_logger(__name__)


def _echo_wrapped(line: str) -> None:
    """Echo *line*, truncating each visual line to the current terminal width."""
    width = shutil.get_terminal_size().columns - 2  # account for scrollbar
    parts = line.split("\n")
    typer.echo("\n".join(p[:width] for p in parts))


# fixme: this is not too interesting; what we want to know is how many FEATURES dev is ahead
#  of main by: for example we could list the feature branches that are in dev with
#  `git log dev --merges --oneline | grep "Merge feat/"`
#  and then compare that to what's still marked as open in the board.
def _dev_ahead_of_main() -> int:
    """Return the number of commits dev is ahead of main (0 if even or behind)."""
    result = subprocess.run(
        ["git", "rev-list", "--count", "main..dev"],
        cwd=_cfg.get().repo_root,
        capture_output=True,
        text=True,
    )
    try:
        return int(result.stdout.strip())
    except (ValueError, AttributeError):
        return 0


def _pending_visions() -> list[str]:
    """Return vision .md filenames (excl. README.md) with no matching board task."""
    vision_dir = _cfg.get().orc_dir / "vision"
    if not vision_dir.is_dir():
        return []
    board = _board._read_board()
    all_task_stems = {
        (t["name"] if isinstance(t, dict) else str(t))
        for tasks in (board.get("open", []), board.get("done", []))
        for t in tasks
    }
    result = []
    for f in sorted(vision_dir.glob("*.md")):
        if f.name.lower().startswith("."):
            continue
        if f.name.lower() == "readme.md":
            continue
        if not any(stem == f.name or stem.startswith(f.stem) for stem in all_task_stems):
            result.append(f.name)
    return result


def _unmerged_feature_branches() -> list[str]:
    """Return all local ``feat/*`` branches not yet merged into dev.

    Respects ``orc-branch-prefix``: when a prefix is configured (e.g. ``orc``),
    branches are listed as ``{prefix}/feat/*``; otherwise ``feat/*``.
    """
    cfg = _cfg.get()
    if cfg.branch_prefix:
        pattern = f"{cfg.branch_prefix}/feat/*"
    else:
        pattern = "feat/*"
    result = subprocess.run(
        ["git", "branch", "--list", pattern],
        cwd=cfg.repo_root,
        capture_output=True,
        text=True,
    )
    branches = [line.strip().lstrip("+* ") for line in result.stdout.splitlines() if line.strip()]
    unmerged = []
    for branch in branches:
        merged = subprocess.run(
            ["git", "merge-base", "--is-ancestor", branch, cfg.work_dev_branch],
            cwd=cfg.repo_root,
        )
        if merged.returncode != 0:
            unmerged.append(branch)
    return unmerged


# Backward-compatible alias used by dispatcher callbacks.
_pending_reviews = _unmerged_feature_branches


def _get_wip_branches(branches: list[str] | None = None) -> list[str]:
    """Return feature branches where the coder has made their exit commit.

    These branches have a ``chore(coder-N.done.NNNN):`` tip commit — the coder
    is finished but QA has not yet run.  They represent work *in progress*
    (awaiting review) from the dispatcher's perspective.

    When *branches* is provided, it is used instead of calling
    :func:`_unmerged_feature_branches`, avoiding a redundant git query.
    """
    if branches is None:
        branches = _unmerged_feature_branches()
    result = []
    for branch in branches:
        last_msg = _git._last_feature_commit_message(branch)
        if _git._classify_last_commit(last_msg) == _LastCommit.CODER_DONE:
            result.append(branch)
    return result


def _get_approved_branches(branches: list[str] | None = None) -> list[str]:
    """Return feature branches that QA has approved and are ready to merge.

    These branches have a ``chore(qa-N.approve.NNNN):`` tip commit — QA passed
    and the branch should be merged into dev.

    When *branches* is provided, it is used instead of calling
    :func:`_unmerged_feature_branches`, avoiding a redundant git query.
    """
    result = []
    if branches is None:
        branches = _unmerged_feature_branches()
    for branch in branches:
        last_msg = _git._last_feature_commit_message(branch)
        if _git._classify_last_commit(last_msg) == _LastCommit.QA_PASSED:
            result.append(branch)
    return result


def _dev_log_since_main() -> list[str]:
    """Return one-line summaries of commits on dev not yet in main."""
    result = subprocess.run(
        ["git", "log", "--oneline", "--no-decorate", "main..dev"],
        cwd=_cfg.get().repo_root,
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        return []
    return result.stdout.strip().splitlines()


def _status(squad: str = "default") -> None:
    messages = tg.get_messages()

    # Build a single work snapshot — used for all display decisions below.
    blocked_agent, blocked_state = _wf._has_unresolved_block(messages)
    stalled = [(blocked_agent, blocked_state)] if blocked_agent else []
    work = Work(
        open_tasks=_board.get_open_tasks(),
        open_visions=_pending_visions(),
        open_todos_and_fixmes=_ctx._scan_todos(_cfg.get().repo_root),
        open_PRs=_pending_reviews(),
        stalled_agents=stalled,
    )

    # Load squad (best-effort — status should degrade gracefully)
    try:
        squad_cfg = load_squad(squad, orc_dir=_cfg.get().orc_dir)
    except Exception:
        squad_cfg = None

    # --- Squad header --------------------------------------------------------
    if squad_cfg:
        coder_label = f"{squad_cfg.coder} coder{'s' if squad_cfg.coder != 1 else ''}"
        qa_label = f"{squad_cfg.qa} QA"
        _echo_wrapped(
            f"Squad: {squad_cfg.name}"
            f"  (1 planner · {coder_label} · {qa_label} · {squad_cfg.timeout_minutes} min)"
        )

    # --- Hard block warning --------------------------------------------------
    if work.hard_blocked:
        hard_agent, _ = work.hard_blocked
        _echo_wrapped(f"\n⛔ Hard block: {hard_agent} is waiting for human intervention.")

    # --- dev vs main ---------------------------------------------------------
    ahead = _dev_ahead_of_main()
    if ahead:
        _echo_wrapped(f"\ndev is {ahead} commit{'s' if ahead != 1 else ''} ahead of main")
        for line in _dev_log_since_main():
            _echo_wrapped(f"  {line}")
        _echo_wrapped("\nRun `orc merge` to fast-forward main.")
    else:
        _echo_wrapped("\nmain is up to date with dev.")

    # --- Per-agent status ----------------------------------------------------
    if squad_cfg:
        coder_tasks: list[tuple[str, str]] = []
        qa_tasks: list[tuple[str, str]] = []
        merge_pending: list[str] = []
        for task in work.open_tasks:
            name = task["name"]
            token, reason = _git._derive_task_state(name)
            if token == AgentRole.CODER:
                coder_tasks.append((name, reason))
            elif token == AgentRole.QA:
                qa_tasks.append((name, _git._feature_branch(name)))
            elif token == _QA_PASSED:
                merge_pending.append(name)

        if not _board.has_open_work():
            planner_note = "ready (visions pending)"
        elif blocked_agent and blocked_state == "soft-blocked":
            planner_note = f"ready to clarify soft-block from {blocked_agent}"
        else:
            planner_note = "idle"

        _echo_wrapped("\nAgent status:")

        sym_p = _ctx._role_symbol(AgentRole.PLANNER)
        sym_c = _ctx._role_symbol(AgentRole.CODER)
        sym_q = _ctx._role_symbol(AgentRole.QA)

        # Compute the longest agent-name string so we can ljust only the ASCII
        # part.  Emoji code-point counts don't match terminal display widths
        # (e.g. "🛠️" is 2 code points but 2 display columns, same as "📋"),
        # so including the symbol in the ljust field breaks alignment.
        all_names = (
            ["planner-1"]
            + [f"coder-{i}" for i in range(1, squad_cfg.coder + 1)]
            + [f"qa-{i}" for i in range(1, squad_cfg.qa + 1)]
        )
        name_width = max(len(n) for n in all_names)

        def _row(sym: str, name: str, note: str) -> str:
            prefix = f"{sym} " if sym else ""
            return f"  {prefix}{name:<{name_width}}  {note}"

        _echo_wrapped(_row(sym_p, "planner-1", planner_note))

        for i in range(1, squad_cfg.coder + 1):
            idx = i - 1
            if idx < len(coder_tasks):
                task_name, _ = coder_tasks[idx]
                note = f"ready (next up: {task_name})"
            else:
                note = "idle  (no work ready)"
            _echo_wrapped(_row(sym_c, f"coder-{i}", note))

        for i in range(1, squad_cfg.qa + 1):
            idx = i - 1
            if idx < len(qa_tasks):
                task_name, branch = qa_tasks[idx]
                note = f"ready (next up: {branch})"
            else:
                note = "idle"
            _echo_wrapped(_row(sym_q, f"qa-{i}", note))

        if merge_pending:
            _echo_wrapped(f"\n  ⟳ Merge pending: {', '.join(merge_pending)}")

    # --- Board summary -------------------------------------------------------
    board = _board._read_board()
    done_tasks = board.get("done", [])

    if work.open_tasks:
        _echo_wrapped("\nPending tasks:")
        for task in work.open_tasks:
            name = task["name"] if isinstance(task, dict) else str(task)
            branch = _git._feature_branch(name)
            if _git._feature_branch_exists(branch):
                last = _git._last_feature_commit_message(branch) or ""
                _echo_wrapped(f"  • {name}  ({branch})  last: {last}")
            else:
                _echo_wrapped(f"  • {name}  (no branch yet)")

    # --- Pending visions -----------------------------------------------------
    if work.open_visions:
        shown = work.open_visions[:5]
        _echo_wrapped(f"\nPending visions ({len(shown)} of {len(work.open_visions)}):")
        for v in shown:
            _echo_wrapped(f"  📄 {v}")

    # --- TODOs / FIXMEs ------------------------------------------------------
    if work.open_todos_and_fixmes:
        shown_t = work.open_todos_and_fixmes[:5]
        total_t = len(work.open_todos_and_fixmes)
        _echo_wrapped(f"\nTODOs / FIXMEs ({len(shown_t)} of {total_t}):")
        for item in shown_t:
            tag = item.get("tag", "TODO")
            path = item.get("file", "?")
            lineno = item.get("line", "?")
            text = item.get("text", "").strip()
            _echo_wrapped(f"  [{tag}] {path}:{lineno}  {text}")

    # --- Branches awaiting QA review -----------------------------------------
    wip = _get_wip_branches(work.open_PRs)
    if wip:
        shown_w = wip[:5]
        _echo_wrapped(f"\nAwaiting review ({len(shown_w)} of {len(wip)}):")
        for branch in shown_w:
            last = _git._last_feature_commit_message(branch) or ""
            _echo_wrapped(f"  🔍 {branch}  last: {last}")

    # --- Branches approved by QA, pending merge ------------------------------
    approved = _get_approved_branches(work.open_PRs)
    if approved:
        shown_a = approved[:5]
        _echo_wrapped(f"\nApproved, pending merge ({len(shown_a)} of {len(approved)}):")
        for branch in shown_a:
            last = _git._last_feature_commit_message(branch) or ""
            _echo_wrapped(f"  🔀 {branch}  last: {last}")

    # --- Last completed tasks (newest first, capped at 5) --------------------
    if done_tasks:
        recent = list(reversed(done_tasks[-5:]))
        _echo_wrapped(f"\nLast completed tasks ({len(recent)} of {len(done_tasks)}):")
        for task in recent:
            name = task.get("name", "?") if isinstance(task, dict) else str(task)
            tag = task.get("commit-tag", "?") if isinstance(task, dict) else "?"
            ts = task.get("timestamp", "") if isinstance(task, dict) else ""
            ts_str = f"  {ts}" if ts else ""
            _echo_wrapped(f"  ✓ {name}  ({tag}){ts_str}")


def _is_tty() -> bool:
    """Return True if stdout is a TTY (enables the interactive TUI)."""
    return sys.stdout.isatty()


@app.command()
def status(
    squad: Annotated[
        str,
        typer.Option(
            "--squad",
            help="Squad profile name used to determine agent slots. Default: 'default'.",
        ),
    ] = "default",
) -> None:
    """Print current workflow state without running any agent."""
    if _is_tty():
        from orc.tui.status_tui import run_status_tui  # noqa: PLC0415

        run_status_tui(squad=squad)
    else:
        return _status(squad=squad)
