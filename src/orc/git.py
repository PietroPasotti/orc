"""orc – git worktree and branch helpers."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import structlog
import yaml

import orc.board as _board
import orc.config as _cfg
from orc.dispatcher import CLOSE_BOARD as _CLOSE_BOARD
from orc.dispatcher import QA_PASSED as _QA_PASSED

logger = structlog.get_logger(__name__)


def _ensure_dev_worktree() -> Path:
    """Ensure the ``dev`` branch and its worktree exist."""
    existing = subprocess.run(
        ["git", "branch", "--list", _cfg.WORK_DEV_BRANCH],
        cwd=_cfg.REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if not existing.stdout.strip():
        subprocess.run(
            ["git", "branch", _cfg.WORK_DEV_BRANCH],
            cwd=_cfg.REPO_ROOT,
            check=True,
        )

    if not _cfg.DEV_WORKTREE.exists():
        subprocess.run(["git", "worktree", "prune"], cwd=_cfg.REPO_ROOT, check=True)
        subprocess.run(
            ["git", "worktree", "add", str(_cfg.DEV_WORKTREE), _cfg.WORK_DEV_BRANCH],
            cwd=_cfg.REPO_ROOT,
            check=True,
        )

    return _cfg.DEV_WORKTREE


def _feature_branch(task_name: str) -> str:
    """Return the feature branch name for *task_name*."""
    return f"feat/{Path(task_name).stem}"


def _feature_worktree_path(task_name: str) -> Path:
    """Return the expected filesystem path of the feature worktree."""
    slug = _feature_branch(task_name).replace("/", "-")
    return _cfg.DEV_WORKTREE.parent / f"{_cfg.REPO_ROOT.name}-{slug}"


def _ensure_feature_worktree(task_name: str) -> Path:
    """Ensure a feature branch and linked worktree exist for *task_name*."""
    branch = _feature_branch(task_name)
    wt_path = _feature_worktree_path(task_name)

    existing = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=_cfg.REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if not existing.stdout.strip():
        subprocess.run(["git", "branch", branch, "main"], cwd=_cfg.REPO_ROOT, check=True)

    if not wt_path.exists():
        subprocess.run(["git", "worktree", "prune"], cwd=_cfg.REPO_ROOT, check=True)
        subprocess.run(
            ["git", "worktree", "add", str(wt_path), branch],
            cwd=_cfg.REPO_ROOT,
            check=True,
        )

    return wt_path


def _close_task_on_board(task_name: str, dev_wt: Path, commit_tag: str = "pending") -> None:
    """Move *task_name* from ``open`` to ``done`` in board.yaml and delete its .md file."""
    try:
        config_rel = _cfg.AGENTS_DIR.relative_to(_cfg.REPO_ROOT)
    except ValueError:
        config_rel = Path(_cfg.AGENTS_DIR.name)
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
    """Merge the feature branch into dev, close the task in board.yaml, and clean up."""
    branch = _feature_branch(task_name)
    wt_path = _feature_worktree_path(task_name)
    dev_wt = _ensure_dev_worktree()

    logger.info("merging feature into dev", feature_branch=branch, dev_branch=_cfg.WORK_DEV_BRANCH)
    subprocess.run(["git", "checkout", _cfg.WORK_DEV_BRANCH], cwd=dev_wt, check=True)
    subprocess.run(
        ["git", "merge", "--no-ff", branch, "-m", f"Merge {branch} into {_cfg.WORK_DEV_BRANCH}"],
        cwd=dev_wt,
        check=True,
    )

    merge_sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=dev_wt,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

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
            cwd=_cfg.REPO_ROOT,
            check=True,
        )

    logger.info("deleting feature branch", branch=branch)
    subprocess.run(["git", "branch", "-d", branch], cwd=_cfg.REPO_ROOT)


def _feature_has_commits_ahead_of_main(branch: str) -> bool:
    """Return True if *branch* has at least one commit not in main."""
    result = subprocess.run(
        ["git", "log", "main.." + branch, "--oneline"],
        cwd=_cfg.REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _feature_merged_into_dev(branch: str) -> bool:
    """Return True if *branch* has been merged into dev (is an ancestor)."""
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", branch, _cfg.WORK_DEV_BRANCH],
        cwd=_cfg.REPO_ROOT,
    )
    return result.returncode == 0


def _feature_branch_exists(branch: str) -> bool:
    """Return True if *branch* exists locally."""
    result = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=_cfg.REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _last_feature_commit_message(branch: str) -> str | None:
    """Return the subject line of the most recent commit on *branch*, or None."""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%s", branch],
        cwd=_cfg.REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or None


def _derive_task_state(task_name: str) -> tuple[str, str]:
    """Inspect the git tree for *task_name* and return ``(token, reason)``."""
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
    """Derive the next-agent token from git for the currently active task."""
    active_task = _board._active_task_name()
    if not active_task:
        return "planner", "no open tasks on board"
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


def _complete_merge(worktree: Path) -> None:
    """Fast-forward merge dev into main, then switch back to dev."""
    subprocess.run(["git", "checkout", "main"], cwd=worktree, check=True)
    subprocess.run(["git", "merge", "--ff-only", _cfg.WORK_DEV_BRANCH], cwd=worktree, check=True)
    subprocess.run(["git", "checkout", _cfg.WORK_DEV_BRANCH], cwd=worktree, check=True)


def _conflict_status(worktree: Path) -> str:
    """Return the output of ``git status --short`` in *worktree*."""
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()
