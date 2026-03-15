"""orc status command."""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Annotated

import structlog
import typer

import orc.config as _cfg
import orc.coordination.board as _board
import orc.coordination.board._board as _board_impl
import orc.engine.context as _ctx
import orc.engine.workflow as _wf
from orc.cli import app
from orc.coordination.board import TaskStatus
from orc.engine.dispatcher import QA_PASSED as _QA_PASSED
from orc.squad import AgentRole, load_squad

logger = structlog.get_logger(__name__)


def _echo_wrapped(line: str) -> None:
    """Echo *line*, truncating each visual line to the current terminal width."""
    width = shutil.get_terminal_size().columns
    parts = line.split("\n")
    typer.echo("\n".join(p[:width] for p in parts))


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
    ready_dir = _cfg.get().vision_dir / "ready"
    if not ready_dir.is_dir():
        return []
    board = _board_impl._read_board()
    all_task_stems = {t.name for t in board.tasks}
    result = []
    for f in sorted(ready_dir.glob("*.md")):
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
    pattern = f"{cfg.branch_prefix}/feat/*" if cfg.branch_prefix else "feat/*"
    result = subprocess.run(
        ["git", "branch", "--list", pattern],
        cwd=cfg.repo_root,
        capture_output=True,
        text=True,
    )
    branches = [line.strip().lstrip("+* ") for line in result.stdout.splitlines() if line.strip()]
    from orc.git import Git

    git = Git(cfg.repo_root)
    return [b for b in branches if not git.is_merged_into(b, cfg.work_dev_branch)]


# Backward-compatible alias used by dispatcher callbacks.
_pending_reviews = _unmerged_feature_branches


def _get_wip_branches(branches: list[str] | None = None) -> list[str]:
    """Return feature branches for tasks in ``in-review`` status (awaiting QA).

    When *branches* is provided, only branches in that list are included.
    """
    result = []
    for task in _board.get_tasks():
        if task.status == TaskStatus.IN_REVIEW:
            branch = _cfg.get().feature_branch(task.name)
            if branches is None or branch in branches:
                result.append(branch)
    return result


def _get_approved_branches(branches: list[str] | None = None) -> list[str]:
    """Return feature branches for tasks in ``done`` status (QA passed, ready to merge).

    When *branches* is provided, only branches in that list are included.
    """
    result = []
    for task in _board.get_tasks():
        if task.status == "done":
            branch = _cfg.get().feature_branch(task.name)
            if branches is None or branch in branches:
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
    open_tasks = _board.get_tasks()
    open_visions = _pending_visions()
    open_todos_and_fixmes = _ctx._scan_todos(_cfg.get().repo_root)
    open_PRs = _pending_reviews()
    blocked_task = next((t.name for t in open_tasks if t.status == "blocked"), None)

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

    # --- Blocked task warning -------------------------------------------------
    if blocked_task:
        _echo_wrapped(f"\n⛔ Blocked: task {blocked_task!r} needs human intervention.")

    # --- dev vs main ---------------------------------------------------------
    features_pending = _wf._features_in_dev_not_main()
    if features_pending:
        n = len(features_pending)
        _echo_wrapped(f"\ndev has {n} feature{'s' if n != 1 else ''} not yet in main:")
        for branch in features_pending:
            _echo_wrapped(f"  {branch}")
        _echo_wrapped("\nRun `orc merge` to fast-forward main.")
    else:
        _echo_wrapped("\nmain is up to date with dev.")

    # --- Per-agent status ----------------------------------------------------
    if squad_cfg:
        coder_tasks: list[tuple[str, str]] = []
        qa_tasks: list[tuple[str, str]] = []
        merge_pending: list[str] = []
        for task in open_tasks:
            name = task.name
            token, reason = _wf._derive_task_state(name, task)
            if token == AgentRole.CODER:
                coder_tasks.append((name, reason))
            elif token == AgentRole.QA:
                qa_tasks.append((name, _cfg.get().feature_branch(name)))
            elif token == _QA_PASSED:
                merge_pending.append(name)

        # TODO: use has_ready_visions instead of has_open_work for the planner check
        if not _board.has_open_work():
            planner_note = "ready (visions pending)"
        elif blocked_task:
            planner_note = f"ready to clarify block on {blocked_task}"
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
    if open_tasks:
        _echo_wrapped("\nPending tasks:")
        for task in open_tasks:
            name = task.name
            status = task.status or TaskStatus.IN_PROGRESS
            branch = _cfg.get().feature_branch(name)
            if _wf._feature_branch_exists(branch):
                _echo_wrapped(f"  • {name}  ({branch})  status: {status}")
            else:
                _echo_wrapped(f"  • {name}  (no branch yet)")

    # --- Pending visions -----------------------------------------------------
    if open_visions:
        shown = open_visions[:5]
        _echo_wrapped(f"\nPending visions ({len(shown)} of {len(open_visions)}):")
        for v in shown:
            _echo_wrapped(f"  📄 {v}")

    # --- TODOs / FIXMEs ------------------------------------------------------
    if open_todos_and_fixmes:
        shown_t = open_todos_and_fixmes[:5]
        total_t = len(open_todos_and_fixmes)
        _echo_wrapped(f"\nTODOs / FIXMEs ({len(shown_t)} of {total_t}):")
        for item in shown_t:
            tag = item.tag
            path = item.file
            lineno = item.line
            text = item.text.strip()
            _echo_wrapped(f"  [{tag}] {path}:{lineno}  {text}")

    # --- Branches awaiting QA review -----------------------------------------
    wip = _get_wip_branches(open_PRs)
    if wip:
        shown_w = wip[:5]
        _echo_wrapped(f"\nAwaiting review ({len(shown_w)} of {len(wip)}):")
        for branch in shown_w:
            _echo_wrapped(f"  🔍 {branch}")

    # --- Branches approved by QA, pending merge ------------------------------
    approved = _get_approved_branches(open_PRs)
    if approved:
        shown_a = approved[:5]
        _echo_wrapped(f"\nApproved, pending merge ({len(shown_a)} of {len(approved)}):")
        for branch in shown_a:
            _echo_wrapped(f"  🔀 {branch}")


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
    plain: Annotated[
        bool,
        typer.Option("--plain", help="Print plain text without launching the TUI."),
    ] = False,
) -> None:
    """Print current workflow state without running any agent."""
    if not plain and _is_tty():
        from orc.cli.tui.status_tui import run_status_tui  # noqa: PLC0415

        run_status_tui(squad=squad)
    else:
        return _status(squad=squad)
