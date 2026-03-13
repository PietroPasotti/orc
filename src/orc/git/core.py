"""orc – git worktree and branch helpers."""

from __future__ import annotations

import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import structlog
import typer
import yaml

import orc.board as _board
import orc.config as _cfg
from orc.engine.state_machine import ACTION_CLOSE_BOARD as _CLOSE_BOARD
from orc.engine.state_machine import LastCommit, WorldState
from orc.engine.state_machine import route as _route
from orc.squad import AgentRole, SquadConfig

logger = structlog.get_logger(__name__)


class MergeConflictError(Exception):
    """Raised when ``git merge --no-ff`` stops with conflicts.

    The merge is left in progress in *worktree* so that a coder agent can
    resolve the conflict markers and run ``git merge --continue``.
    """

    def __init__(self, branch: str, worktree: Path, status_output: str) -> None:
        self.branch = branch
        self.worktree = worktree
        self.status_output = status_output
        super().__init__(f"merge conflict on {branch!r} in {worktree}")


def _default_branch() -> str:
    """Return the repo's default branch name (e.g. 'main' or 'master')."""
    result = subprocess.run(
        ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        cwd=_cfg.get().repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        # "origin/main" -> "main"
        return result.stdout.strip().split("/", 1)[-1]
    # Fallback: use the current branch name from HEAD (works in fresh local repos)
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=_cfg.get().repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _ensure_dev_worktree() -> Path:
    """Ensure the ``dev`` branch and its worktree exist."""
    cfg = _cfg.get()
    existing = subprocess.run(
        ["git", "branch", "--list", cfg.work_dev_branch],
        cwd=cfg.repo_root,
        capture_output=True,
        text=True,
    )
    if not existing.stdout.strip():
        subprocess.run(
            ["git", "branch", cfg.work_dev_branch],
            cwd=cfg.repo_root,
            check=True,
        )

    if not cfg.dev_worktree.exists():
        cfg.dev_worktree.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "worktree", "prune"], cwd=cfg.repo_root, check=True)
        subprocess.run(
            ["git", "worktree", "add", str(cfg.dev_worktree), cfg.work_dev_branch],
            cwd=cfg.repo_root,
            check=True,
        )

    return cfg.dev_worktree


def _is_worktree_dirty(worktree: Path) -> bool:
    """Return True if *worktree* has any uncommitted changes."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _merge_in_progress(worktree: Path) -> bool:
    """Return True if a merge is currently paused in *worktree*."""
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    git_dir = Path(result.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = worktree / git_dir
    return (git_dir / "MERGE_HEAD").exists()


def _feature_branch(task_name: str) -> str:
    """Return the feature branch name for *task_name*.

    When ``orc-branch-prefix`` is set, the branch is prefixed:
    e.g. prefix ``"orc"`` → ``"orc/feat/0001-foo"``; no prefix → ``"feat/0001-foo"``.
    """
    branch = f"feat/{Path(task_name).stem}"
    if _cfg.get().branch_prefix:
        return f"{_cfg.get().branch_prefix}/{branch}"
    return branch


def _feature_worktree_path(task_name: str) -> Path:
    """Return the expected filesystem path of the feature worktree.

    Worktrees are placed under ``WORKTREE_BASE / task_stem``,
    e.g. ``.orc/worktrees/0001-foo`` for task ``0001-foo.md``.
    """
    return _cfg.get().worktree_base / Path(task_name).stem


def _ensure_feature_worktree(task_name: str) -> Path:
    """Ensure a feature branch and linked worktree exist for *task_name*."""
    branch = _feature_branch(task_name)
    wt_path = _feature_worktree_path(task_name)

    existing = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=_cfg.get().repo_root,
        capture_output=True,
        text=True,
    )
    if not existing.stdout.strip():
        subprocess.run(
            ["git", "branch", branch, _default_branch()], cwd=_cfg.get().repo_root, check=True
        )

    if not wt_path.exists():
        wt_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "worktree", "prune"], cwd=_cfg.get().repo_root, check=True)
        subprocess.run(
            ["git", "worktree", "add", str(wt_path), branch],
            cwd=_cfg.get().repo_root,
            check=True,
        )

    return wt_path


def _close_task_on_board(task_name: str, dev_wt: Path, commit_tag: str = "pending") -> None:
    """Move *task_name* from ``open`` to ``done`` in board.yaml and delete its .md file."""
    cfg = _cfg.get()
    try:
        config_rel = cfg.orc_dir.relative_to(cfg.repo_root)
    except ValueError:
        config_rel = Path(cfg.orc_dir.name)
    board_path = dev_wt / config_rel / "work" / "board.yaml"
    if not board_path.exists():
        logger.warning("board.yaml not found in dev worktree, skipping board update")
        return

    board = yaml.safe_load(board_path.read_text()) or {}
    board.setdefault("open", [])
    board.setdefault("done", [])

    board["open"] = [
        t for t in board["open"] if (t["name"] if isinstance(t, dict) else str(t)) != task_name
    ]

    board["done"].append(
        {
            "name": task_name,
            "commit-tag": commit_tag,
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )

    board_path.write_text(yaml.dump(board, default_flow_style=False, allow_unicode=True))

    task_md = dev_wt / config_rel / "work" / task_name
    if task_md.exists():
        task_md.unlink()
        logger.info("deleted task file", path=str(task_md))


def _merge_feature_into_dev(task_name: str) -> None:
    """Merge the feature branch into dev, close the task in board.yaml, and clean up.

    Before merging, if the dev worktree has uncommitted changes (e.g. from a
    previously aborted merge), they are discarded with ``git reset --hard HEAD``
    and a warning is logged.

    If the merge produces conflicts the dev worktree is left in mid-merge state
    and :class:`MergeConflictError` is raised so the caller can delegate conflict
    resolution to a coder agent (which should run ``git merge --continue`` after
    fixing the markers).
    """
    branch = _feature_branch(task_name)
    wt_path = _feature_worktree_path(task_name)
    dev_wt = _ensure_dev_worktree()

    if _is_worktree_dirty(dev_wt):
        logger.warning(
            "dev worktree is dirty before merge — resetting to HEAD",
            worktree=str(dev_wt),
            branch=branch,
        )
        subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=dev_wt, check=True)

    cfg = _cfg.get()
    logger.info("merging feature into dev", feature_branch=branch, dev_branch=cfg.work_dev_branch)
    subprocess.run(["git", "checkout", cfg.work_dev_branch], cwd=dev_wt, check=True)
    merge_result = subprocess.run(
        ["git", "merge", "--no-ff", branch, "-m", f"Merge {branch} into {cfg.work_dev_branch}"],
        cwd=dev_wt,
    )
    if merge_result.returncode != 0:
        status_output = _conflict_status(dev_wt)
        raise MergeConflictError(branch, dev_wt, status_output)

    merge_sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=dev_wt,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    _close_task_on_board(task_name, dev_wt, commit_tag=merge_sha)
    try:
        config_rel = cfg.orc_dir.relative_to(cfg.repo_root)
    except ValueError:
        config_rel = Path(cfg.orc_dir.name)
    board_path = dev_wt / config_rel / "work" / "board.yaml"
    if board_path.exists():
        subprocess.run(["git", "add", str(config_rel / "work")], cwd=dev_wt, check=True)
        subprocess.run(
            ["git", "commit", "-m", f"chore(orc): close task {Path(task_name).stem}"],
            cwd=dev_wt,
            check=True,
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": "orc",
                "GIT_AUTHOR_EMAIL": "orc@orc.local",
                "GIT_COMMITTER_NAME": "orc",
                "GIT_COMMITTER_EMAIL": "orc@orc.local",
            },
        )
        logger.info("board updated and committed", task=task_name, commit_tag=merge_sha)

    if wt_path.exists():
        logger.info("removing feature worktree", path=str(wt_path))
        subprocess.run(
            ["git", "worktree", "remove", str(wt_path), "--force"],
            cwd=cfg.repo_root,
            check=True,
        )

    subprocess.run(["git", "worktree", "prune"], cwd=cfg.repo_root, check=True)

    logger.info("deleting feature branch", branch=branch)
    subprocess.run(["git", "branch", "-D", branch], cwd=cfg.repo_root, check=True)


def _feature_has_commits_ahead_of_main(branch: str) -> bool:
    """Return True if *branch* has at least one commit not in the default branch."""
    result = subprocess.run(
        ["git", "log", _default_branch() + ".." + branch, "--oneline"],
        cwd=_cfg.get().repo_root,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _feature_merged_into_dev(branch: str) -> bool:
    """Return True if *branch* has been merged into dev (is an ancestor)."""
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", branch, _cfg.get().work_dev_branch],
        cwd=_cfg.get().repo_root,
    )
    return result.returncode == 0


def _feature_branch_exists(branch: str) -> bool:
    """Return True if *branch* exists locally."""
    result = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=_cfg.get().repo_root,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _last_feature_commit_message(branch: str) -> str | None:
    """Return the subject line of the most recent commit on *branch*, or None."""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%s", branch],
        cwd=_cfg.get().repo_root,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or None


_EXIT_SCOPE_RE = re.compile(
    r"^chore\((?P<agent_id>[a-z]+-\d+)\.(?P<action>[a-z]+)\.(?P<task_code>\d{4})\):"
)
"""Regex for the structured exit-commit scope: ``chore(<agent-id>.<action>.<task-code>):``."""


def _parse_exit_scope(subject: str) -> tuple[str, str, str] | None:
    """Parse a structured exit commit subject into ``(agent_id, action, task_code)``.

    Returns ``None`` for subjects that do not match the exit-commit format.

    Examples
    --------
    >>> _parse_exit_scope("chore(coder-1.done.0002): finished task")
    ('coder-1', 'done', '0002')
    >>> _parse_exit_scope("chore(qa-2.approve.0003): all green")
    ('qa-2', 'approve', '0003')
    >>> _parse_exit_scope("feat: add something")
    None
    """
    m = _EXIT_SCOPE_RE.match(subject)
    if m is None:
        return None
    return m.group("agent_id"), m.group("action"), m.group("task_code")


def _classify_last_commit(last_msg: str | None) -> LastCommit:
    """Map a raw commit subject to a :class:`~orc.engine.state_machine.LastCommit` value.

    This is the canonical commit classifier — :func:`_derive_task_state` uses it
    so that :func:`~orc.engine.state_machine.route` remains the single
    source of truth for routing decisions.
    """
    if not last_msg:
        return LastCommit.CODER_WORK

    parsed = _parse_exit_scope(last_msg)
    if parsed is not None:
        _, action, _ = parsed
        if action == "approve":
            return LastCommit.QA_PASSED
        if action == "reject":
            return LastCommit.QA_OTHER
        if action == "done":
            return LastCommit.CODER_DONE

    return LastCommit.CODER_WORK


def _derive_task_state(task_name: str) -> tuple[str, str]:
    """Inspect the git tree for *task_name* and return ``(token, reason)``.

    Collects live git state, then delegates the routing decision to
    :func:`~orc.engine.state_machine.route` — the single source of truth.
    """
    branch = _feature_branch(task_name)

    branch_exists = _feature_branch_exists(branch)
    logger.debug(
        "derive_task_state: branch exists", task=task_name, branch=branch, exists=branch_exists
    )

    if not branch_exists:
        logger.debug("derive_task_state: branch absent — dispatch coder", task=task_name)
        return AgentRole.CODER, f"feature branch {branch!r} does not exist yet"

    has_commits = _feature_has_commits_ahead_of_main(branch)
    logger.debug(
        "derive_task_state: commits ahead of main",
        task=task_name,
        branch=branch,
        has_commits=has_commits,
    )

    if not has_commits:
        if _feature_merged_into_dev(branch):
            logger.info(
                "derive_task_state: branch exists but already merged into dev — closing board",
                task=task_name,
                branch=branch,
            )
            return _CLOSE_BOARD, f"branch {branch!r} already merged into dev but board not updated"
        return AgentRole.CODER, f"feature branch {branch!r} has no commits ahead of main"

    last_msg = _last_feature_commit_message(branch)
    logger.debug("derive_task_state: last commit", task=task_name, branch=branch, last_msg=last_msg)

    last_commit = _classify_last_commit(last_msg)
    world_state = WorldState(
        has_open_task=True,
        branch_exists=True,
        commits_ahead=True,
        last_commit=last_commit,
    )
    action = _route(world_state)  # single source of truth

    if last_commit == LastCommit.QA_PASSED:
        reason = f"qa approved on {branch!r} — ready to merge"
    elif last_commit == LastCommit.CODER_DONE:
        reason = f"coder finished {branch!r}, awaiting review"
    elif last_commit == LastCommit.QA_OTHER:
        reason = f"qa rejected {branch!r}: {last_msg!r}"
    else:
        reason = f"coder has uncommitted work on {branch!r} — not yet signalled done"

    return action, reason  # type: ignore[return-value]


def _derive_state_from_git() -> tuple[str, str]:
    """Derive the next-agent token from git for the currently active task."""
    active_task = _board._active_task_name()
    if not active_task:
        return AgentRole.PLANNER, "no open tasks on board"
    return _derive_task_state(active_task)


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


def _complete_merge() -> bool:
    """Fast-forward merge dev into main from the repo root worktree.

    The root worktree always has the default branch checked out, so no
    checkout is needed before or after the merge.

    Returns True if a merge was performed, False if already up to date.
    """
    cfg = _cfg.get()
    result = subprocess.run(
        ["git", "merge", "--ff-only", cfg.work_dev_branch],
        cwd=cfg.repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return "Already up to date" not in result.stdout


def _conflict_status(worktree: Path) -> str:
    """Return the output of ``git status --short`` in *worktree*."""
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _count_features_done() -> int:
    """Count ``Merge feat/NNNN-*`` commits on dev that are not yet on main."""
    cfg = _cfg.get()
    result = subprocess.run(
        ["git", "log", "--merges", "--oneline", f"main..{cfg.work_dev_branch}"],
        cwd=cfg.repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return 0
    return sum(1 for line in result.stdout.splitlines() if re.search(r"feat/\d{4}-", line))


def _rebase_dev_on_main(messages: list, squad_cfg: SquadConfig | None = None) -> None:
    """Rebase dev on top of main so every session starts with the latest instructions."""
    import orc.engine.context as _ctx

    dev_worktree = _ensure_dev_worktree()

    result = subprocess.run(
        ["git", "rebase", "--autostash", "main"], cwd=dev_worktree, capture_output=True, text=True
    )
    if result.returncode == 0:
        typer.echo("✓ dev rebased on main.")
        return

    status_output = _conflict_status(dev_worktree)
    typer.echo(f"⚠ Startup rebase conflict:\n{status_output}\nDelegating to coder agent…")

    conflict_extra = (
        "## Startup rebase conflict — your task\n\n"
        f"A `git rebase main` of the `{_cfg.get().work_dev_branch}` "
        "branch was attempted at session "
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

    coder_model = squad_cfg.model(AgentRole.CODER) if squad_cfg is not None else _ctx._DEFAULT_MODEL
    model, context = _ctx.build_agent_context(
        AgentRole.CODER, messages, extra=conflict_extra, model=coder_model
    )
    rc = _ctx.invoke_agent(AgentRole.CODER, context, model)

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
