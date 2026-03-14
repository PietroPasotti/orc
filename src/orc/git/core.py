"""orc – git worktree and branch helpers."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import structlog

import orc.board as _board
import orc.config as _cfg
from orc.engine.state_machine import ACTION_CLOSE_BOARD as _CLOSE_BOARD
from orc.engine.state_machine import LastCommit, WorldState
from orc.engine.state_machine import route as _route
from orc.squad import AgentRole

logger = structlog.get_logger(__name__)


# Map board task status → LastCommit enum for state machine routing.
_STATUS_TO_LAST_COMMIT: dict[str, LastCommit] = {
    "planned": LastCommit.CODER_WORK,
    "coding": LastCommit.CODER_WORK,
    "review": LastCommit.CODER_DONE,
    "approved": LastCommit.QA_PASSED,
    "rejected": LastCommit.QA_OTHER,
    "blocked": LastCommit.CODER_WORK,
    "soft-blocked": LastCommit.CODER_WORK,
    "merged": LastCommit.QA_PASSED,
}


class UntrackedMergeBlockError(Exception):
    """Raised when ``git merge --ff-only`` is blocked by untracked files in the main worktree."""

    def __init__(self, files: list[str]) -> None:
        self.files = files
        super().__init__(f"untracked files would be overwritten by merge: {files}")


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
            capture_output=True,
        )

    if not cfg.dev_worktree.exists():
        cfg.dev_worktree.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "worktree", "prune"], cwd=cfg.repo_root, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "worktree", "add", str(cfg.dev_worktree), cfg.work_dev_branch],
            cwd=cfg.repo_root,
            check=True,
            capture_output=True,
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
            ["git", "branch", branch, _default_branch()],
            cwd=_cfg.get().repo_root,
            check=True,
            capture_output=True,
        )

    if not wt_path.exists():
        wt_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "worktree", "prune"], cwd=_cfg.get().repo_root, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "worktree", "add", str(wt_path), branch],
            cwd=_cfg.get().repo_root,
            check=True,
            capture_output=True,
        )

    return wt_path


def _close_task_on_board(task_name: str, commit_tag: str = "pending") -> None:
    """Move *task_name* from ``open`` to ``done`` in the board and delete its task file."""
    board = _board._read_board()
    board["open"] = [
        t
        for t in board.get("open", [])
        if (t["name"] if isinstance(t, dict) else str(t)) != task_name
    ]
    board.setdefault("done", [])
    board["done"].append(
        {
            "name": task_name,
            "commit-tag": commit_tag,
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )
    _board._write_board(board)
    _board._get_manager().delete_task_file(task_name)


def _merge_feature_into_dev(task_name: str) -> None:
    """Merge the feature branch into dev, close the task on the board, and clean up.

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
        subprocess.run(
            ["git", "reset", "--hard", "HEAD"], cwd=dev_wt, check=True, capture_output=True
        )

    cfg = _cfg.get()
    logger.info("merging feature into dev", feature_branch=branch, dev_branch=cfg.work_dev_branch)
    subprocess.run(
        ["git", "checkout", cfg.work_dev_branch], cwd=dev_wt, check=True, capture_output=True
    )
    merge_result = subprocess.run(
        ["git", "merge", "--no-ff", branch, "-m", f"Merge {branch} into {cfg.work_dev_branch}"],
        cwd=dev_wt,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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

    _close_task_on_board(task_name, commit_tag=merge_sha)
    logger.info("board updated", task=task_name, commit_tag=merge_sha)

    if wt_path.exists():
        logger.info("removing feature worktree", path=str(wt_path))
        subprocess.run(
            ["git", "worktree", "remove", str(wt_path), "--force"],
            cwd=cfg.repo_root,
            check=True,
            capture_output=True,
        )

    subprocess.run(["git", "worktree", "prune"], cwd=cfg.repo_root, check=True, capture_output=True)

    logger.info("deleting feature branch", branch=branch)
    subprocess.run(
        ["git", "branch", "-D", branch], cwd=cfg.repo_root, check=True, capture_output=True
    )


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


def _derive_task_state(task_name: str) -> tuple[str, str]:
    """Inspect the git tree + board for *task_name* and return ``(token, reason)``.

    Git branch checks determine whether work has started and completed.
    Routing is delegated to :func:`~orc.engine.state_machine.route` —
    the single source of truth.  Task status is read from the board.
    """
    branch = _feature_branch(task_name)

    branch_exists = _feature_branch_exists(branch)
    logger.debug(
        "derive_task_state: branch exists", task=task_name, branch=branch, exists=branch_exists
    )

    if not branch_exists:
        return AgentRole.CODER, f"feature branch {branch!r} does not exist yet"

    has_commits = _feature_has_commits_ahead_of_main(branch)
    if not has_commits:
        if _feature_merged_into_dev(branch):
            logger.info(
                "derive_task_state: already merged into dev — closing board",
                task=task_name,
                branch=branch,
            )
            return _CLOSE_BOARD, f"branch {branch!r} already merged into dev but board not updated"
        return AgentRole.CODER, f"feature branch {branch!r} has no commits ahead of main"

    task_data = _board.get_task(task_name)
    status = (task_data or {}).get("status") or "coding"
    last_commit = _STATUS_TO_LAST_COMMIT.get(status, LastCommit.CODER_WORK)
    logger.debug(
        "derive_task_state: board status", task=task_name, status=status, last_commit=last_commit
    )

    world_state = WorldState(
        has_open_task=True, branch_exists=True, commits_ahead=True, last_commit=last_commit
    )
    action = _route(world_state)

    _REASONS = {
        "review": f"coder finished {branch!r}, awaiting QA",
        "approved": f"qa approved {branch!r} — ready to merge",
        "rejected": f"qa rejected {branch!r} — back to coder",
    }
    reason = _REASONS.get(status, f"{branch!r} status={status!r}")
    return action, reason  # type: ignore[return-value]


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


def _parse_untracked_files(stderr: str) -> list[str]:
    """Extract filenames from a git 'untracked files would be overwritten' error message."""
    files: list[str] = []
    in_list = False
    for line in stderr.splitlines():
        if "untracked working tree files would be overwritten" in line:
            in_list = True
            continue
        if in_list:
            if line.startswith("\t") and not line.strip().startswith("Please"):
                files.append(line.strip())
            else:
                in_list = False
    return files


def _complete_merge() -> bool:
    """Fast-forward merge dev into main from the repo root worktree.

    The root worktree always has the default branch checked out, so no
    checkout is needed before or after the merge.

    Returns True if a merge was performed, False if already up to date.

    Raises :class:`UntrackedMergeBlockError` if untracked files would be
    overwritten, so the caller can surface a clear message to the user.
    """
    cfg = _cfg.get()
    result = subprocess.run(
        ["git", "merge", "--ff-only", cfg.work_dev_branch],
        cwd=cfg.repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        if "untracked working tree files would be overwritten" in result.stderr:
            files = _parse_untracked_files(result.stderr)
            raise UntrackedMergeBlockError(files)
        raise subprocess.CalledProcessError(
            result.returncode, result.args, result.stdout, result.stderr
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


def _rebase_on_main(worktree: Path) -> tuple[bool, str]:
    """Attempt to rebase *worktree*'s current branch on top of ``main``.

    Returns ``(True, "")`` on success, or ``(False, conflict_status)`` when
    the rebase stops with conflicts, where *conflict_status* is the output of
    ``git status --short`` in *worktree*.
    """
    result = subprocess.run(
        ["git", "rebase", "--autostash", "main"],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return True, ""
    return False, _conflict_status(worktree)


def _count_features_done() -> int:
    """Count feature-merge commits on ``dev`` that are not yet on ``main``.

    A "feature done" commit is a merge commit whose message matches
    ``Merge feat/NNNN-*``.
    """
    cfg = _cfg.get()
    result = subprocess.run(
        [
            "git",
            "log",
            cfg.work_dev_branch,
            "--not",
            "main",
            "--merges",
            "--oneline",
            "--grep",
            "^Merge feat/",
        ],
        cwd=cfg.repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return 0
    lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
    return len(lines)
