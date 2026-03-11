"""orc status command."""

from __future__ import annotations

import subprocess
from typing import Annotated

import structlog
import typer

import orc.board as _board
import orc.config as _cfg
import orc.context as _ctx
import orc.git as _git
import orc.workflow as _wf
from orc import telegram as tg
from orc.cli import app
from orc.squad import load_squad

logger = structlog.get_logger(__name__)


def _dev_ahead_of_main() -> int:
    """Return the number of commits dev is ahead of main (0 if even or behind)."""
    result = subprocess.run(
        ["git", "rev-list", "--count", "main..dev"],
        cwd=_cfg.REPO_ROOT,
        capture_output=True,
        text=True,
    )
    try:
        return int(result.stdout.strip())
    except (ValueError, AttributeError):
        return 0


def _pending_visions() -> list[str]:
    """Return vision .md filenames (excl. README.md) with no matching board task."""
    vision_dir = _cfg.AGENTS_DIR / "vision"
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
        if f.name.lower() == "readme.md":
            continue
        if not any(stem == f.name or stem.startswith(f.stem) for stem in all_task_stems):
            result.append(f.name)
    return result


def _pending_reviews() -> list[str]:
    """Return feat/* branches that exist locally but are not yet merged into dev."""
    result = subprocess.run(
        ["git", "branch", "--list", "feat/*"],
        cwd=_cfg.REPO_ROOT,
        capture_output=True,
        text=True,
    )
    branches = [line.strip().lstrip("* ") for line in result.stdout.splitlines() if line.strip()]
    unmerged = []
    for branch in branches:
        merged = subprocess.run(
            ["git", "merge-base", "--is-ancestor", branch, _cfg.WORK_DEV_BRANCH],
            cwd=_cfg.REPO_ROOT,
        )
        if merged.returncode != 0:
            unmerged.append(branch)
    return unmerged


def _dev_log_since_main() -> list[str]:
    """Return one-line summaries of commits on dev not yet in main."""
    result = subprocess.run(
        ["git", "log", "--oneline", "--no-decorate", "main..dev"],
        cwd=_cfg.REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        return []
    return result.stdout.strip().splitlines()


def _status(squad: str = "default") -> None:
    messages = tg.get_messages()
    blocked_agent, blocked_state = _wf._has_unresolved_block(messages)

    # Load squad (best-effort — status should degrade gracefully)
    try:
        squad_cfg = load_squad(squad, agents_dir=_cfg.AGENTS_DIR)
    except Exception:
        squad_cfg = None

    # --- Squad header --------------------------------------------------------
    if squad_cfg:
        coder_label = f"{squad_cfg.coder} coder{'s' if squad_cfg.coder != 1 else ''}"
        qa_label = f"{squad_cfg.qa} QA"
        typer.echo(
            f"Squad  : {squad_cfg.name}"
            f"  (1 planner · {coder_label} · {qa_label} · {squad_cfg.timeout_minutes} min)"
        )

    # --- Hard block warning --------------------------------------------------
    if blocked_agent and blocked_state == "blocked":
        typer.echo(f"\n⛔ Hard block: {blocked_agent} is waiting for human intervention.")

    # --- Per-agent status ----------------------------------------------------
    if squad_cfg:
        coder_tasks: list[tuple[str, str]] = []
        qa_tasks: list[tuple[str, str]] = []
        merge_pending: list[str] = []
        for task in _board.get_open_tasks():
            name = task["name"]
            token, reason = _git._derive_task_state(name)
            if token == "coder":
                coder_tasks.append((name, reason))
            elif token == "qa":
                qa_tasks.append((name, _git._feature_branch(name)))
            elif token == _git._QA_PASSED:
                merge_pending.append(name)

        if not _board.has_open_work():
            planner_note = "ready to plan  (board empty)"
        elif blocked_agent and blocked_state == "soft-blocked":
            planner_note = f"ready to clarify soft-block from {blocked_agent}"
        else:
            planner_note = "idle"

        width = 12
        typer.echo("\nAgent status:")

        sym_p = _ctx._role_symbol("planner")
        sym_c = _ctx._role_symbol("coder")
        sym_q = _ctx._role_symbol("qa")

        label_p = f"{sym_p} planner-1" if sym_p else "planner-1"
        typer.echo(f"  {label_p:<{width}}  {planner_note}")

        for i in range(1, squad_cfg.coder + 1):
            idx = i - 1
            if idx < len(coder_tasks):
                name, _ = coder_tasks[idx]
                note = f"ready to pick  {name}"
            else:
                note = "idle  (no work ready)"
            label = f"{sym_c} coder-{i}" if sym_c else f"coder-{i}"
            typer.echo(f"  {label:<{width}}  {note}")

        for i in range(1, squad_cfg.qa + 1):
            idx = i - 1
            if idx < len(qa_tasks):
                name, branch = qa_tasks[idx]
                note = f"ready to review  {branch}"
            else:
                note = "idle"
            label = f"{sym_q} qa-{i}" if sym_q else f"qa-{i}"
            typer.echo(f"  {label:<{width}}  {note}")

        if merge_pending:
            typer.echo(f"\n  ⟳ Merge pending: {', '.join(merge_pending)}")

    # --- Board summary -------------------------------------------------------
    board = _board._read_board()
    open_tasks = board.get("open", [])
    done_tasks = board.get("done", [])

    if open_tasks:
        typer.echo("\nPending tasks:")
        for task in open_tasks:
            name = task["name"] if isinstance(task, dict) else str(task)
            branch = _git._feature_branch(name)
            if _git._feature_branch_exists(branch):
                last = _git._last_feature_commit_message(branch) or ""
                typer.echo(f"  • {name}  ({branch})  last: {last}")
            else:
                typer.echo(f"  • {name}  (no branch yet)")

    # --- dev vs main ---------------------------------------------------------
    ahead = _dev_ahead_of_main()
    if ahead:
        typer.echo(f"\ndev is {ahead} commit{'s' if ahead != 1 else ''} ahead of main")
        for line in _dev_log_since_main():
            typer.echo(f"  {line}")
        typer.echo("\nRun `orc merge` to fast-forward main.")
    else:
        typer.echo("\nmain is up to date with dev.")

    # --- Pending visions -----------------------------------------------------
    visions = _pending_visions()
    if visions:
        shown = visions[:5]
        typer.echo(f"\nPending visions ({len(shown)} of {len(visions)}):")
        for v in shown:
            typer.echo(f"  📄 {v}")

    # --- Pending reviews (unmerged feat branches) ----------------------------
    reviews = _pending_reviews()
    if reviews:
        shown_r = reviews[:5]
        typer.echo(f"\nPending reviews ({len(shown_r)} of {len(reviews)}):")
        for branch in shown_r:
            last = _git._last_feature_commit_message(branch) or ""
            typer.echo(f"  🔀 {branch}  last: {last}")

    # --- Last completed tasks (newest first, capped at 5) --------------------
    if done_tasks:
        recent = list(reversed(done_tasks[-5:]))
        typer.echo(f"\nLast completed tasks ({len(recent)} of {len(done_tasks)}):")
        for task in recent:
            name = task.get("name", "?") if isinstance(task, dict) else str(task)
            tag = task.get("commit-tag", "?") if isinstance(task, dict) else "?"
            ts = task.get("timestamp", "") if isinstance(task, dict) else ""
            ts_str = f"  {ts}" if ts else ""
            typer.echo(f"  ✓ {name}  ({tag}){ts_str}")


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
    return _status(squad=squad)
