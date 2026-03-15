"""orc – Git operations.

A thin, project-agnostic wrapper around the git command-line tool.  All
methods accept concrete values (paths, branch names) rather than reading
from a project configuration object — making it trivially testable and
reusable outside the orc context.

Instantiate :class:`Git` with the working directory for git commands
(repo root *or* a linked worktree — they are interchangeable for most
operations)::

    git = Git(repo_root)         # branch/worktree management
    git_dev = Git(dev_worktree)  # operations inside a specific worktree
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class MergeConflictError(Exception):
    """Raised when ``git merge --no-ff`` stops with conflicts.

    The merge is left in progress so that a coder agent can resolve the
    conflict markers and run ``git merge --continue``.
    """

    def __init__(self, branch: str, worktree: Path, status_output: str) -> None:
        self.branch = branch
        self.worktree = worktree
        self.status_output = status_output
        super().__init__(f"merge conflict on {branch!r} in {worktree}")


class RebaseConflictError(Exception):
    """Raised when ``git rebase`` stops with conflicts.

    The rebase is left in progress in *worktree*.
    """

    def __init__(self, worktree: Path, status_output: str) -> None:
        self.worktree = worktree
        self.status_output = status_output
        super().__init__(f"rebase conflict in {worktree}")


class UntrackedMergeBlockError(Exception):
    """Raised when ``git merge --ff-only`` is blocked by untracked files."""

    def __init__(self, files: list[str]) -> None:
        self.files = files
        super().__init__(f"untracked files would be overwritten by merge: {files}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_untracked_files(stderr: str) -> list[str]:
    """Extract filenames from a git 'untracked files would be overwritten' message."""
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


# ---------------------------------------------------------------------------
# Git class
# ---------------------------------------------------------------------------


class Git:
    """Thin wrapper around the git CLI for a specific working directory.

    Parameters
    ----------
    root:
        Working directory for git commands.  This can be the repository
        root or any linked worktree — they are equivalent for most
        operations.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def _run_subprocess(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        cmd = ["git", *args]
        logger.debug("git", cmd=cmd, cwd=str(self.root))
        result = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True, check=check)
        logger.debug("git done", cmd=cmd, returncode=result.returncode)
        return result

    # ── State queries ─────────────────────────────────────────────────────

    def default_branch(self) -> str:
        """Return the repo's default branch name (e.g. ``'main'`` or ``'master'``)."""
        result = self._run_subprocess(
            "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False
        )
        if result.returncode == 0:
            # "origin/main" -> "main"
            return result.stdout.strip().split("/", 1)[-1]
        # Fallback for repos without a configured remote HEAD
        return self._run_subprocess("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    def _git_dir(self) -> Path:
        result = self._run_subprocess("rev-parse", "--git-dir")
        git_dir = Path(result.stdout.strip())
        return git_dir if git_dir.is_absolute() else self.root / git_dir

    def is_dirty(self) -> bool:
        """Return ``True`` if there are any uncommitted changes."""
        return bool(self.status_short())

    def is_merge_in_progress(self) -> bool:
        """Return ``True`` if a ``git merge`` is currently paused."""
        return (self._git_dir() / "MERGE_HEAD").exists()

    def is_rebase_in_progress(self) -> bool:
        """Return ``True`` if a ``git rebase`` is currently paused."""
        gd = self._git_dir()
        return (gd / "rebase-merge").exists() or (gd / "rebase-apply").exists()

    def status_short(self) -> str:
        """Return the output of ``git status --short``."""
        return self._run_subprocess("status", "--short").stdout.strip()

    def rev_parse_short(self, ref: str = "HEAD") -> str:
        """Return the abbreviated SHA of *ref*."""
        return self._run_subprocess("rev-parse", "--short", ref).stdout.strip()

    # ── Branch operations ─────────────────────────────────────────────────

    def branch_exists(self, name: str) -> bool:
        """Return ``True`` if *name* exists as a local branch."""
        return bool(self._run_subprocess("branch", "--list", name).stdout.strip())

    def branch_create(self, name: str, start_point: str) -> None:
        """Create local branch *name* at *start_point*."""
        self._run_subprocess("branch", name, start_point)

    def branch_delete(self, name: str, force: bool = False) -> None:
        """Delete local branch *name*."""
        self._run_subprocess("branch", "-D" if force else "-d", name)

    def has_commits_ahead_of(self, branch: str, base: str) -> bool:
        """Return ``True`` if *branch* has commits not present in *base*."""
        return bool(self._run_subprocess("log", f"{base}..{branch}", "--oneline").stdout.strip())

    def is_merged_into(self, branch: str, target: str) -> bool:
        """Return ``True`` if *branch* is an ancestor of *target*."""
        result = self._run_subprocess("merge-base", "--is-ancestor", branch, target, check=False)
        return result.returncode == 0

    def log_merges_oneline(self, range_: str) -> list[str]:
        """Return one-line merge-commit subjects for commits in *range_*.

        *range_* is any ``git log`` range expression, e.g. ``'main..dev'``.
        Returns an empty list if git exits non-zero.
        """
        result = self._run_subprocess("log", "--merges", "--oneline", range_, check=False)
        if result.returncode != 0:
            return []
        return result.stdout.splitlines()

    # ── Worktree operations ───────────────────────────────────────────────

    def worktree_add(self, path: Path, branch: str) -> None:
        """Add a linked worktree at *path* checked out at *branch*."""
        self._run_subprocess("worktree", "add", str(path), branch)

    def worktree_remove(self, path: Path, force: bool = True) -> None:
        """Remove the linked worktree rooted at *path*."""
        args = ["worktree", "remove", str(path)]
        if force:
            args.append("--force")
        self._run_subprocess(*args)

    def worktree_prune(self) -> None:
        """Prune stale worktree administrative files."""
        self._run_subprocess("worktree", "prune")

    def ensure_worktree(self, path: Path, branch: str, from_branch: str | None = None) -> None:
        """Ensure *branch* and its linked worktree at *path* exist.

        If *branch* does not exist locally it is created from *from_branch*
        (or the repo's default branch when *from_branch* is omitted).
        If the worktree directory does not exist it is created and linked.
        """
        if not self.branch_exists(branch):
            self.branch_create(branch, from_branch or self.default_branch())
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            self.worktree_prune()
            self.worktree_add(path, branch)

    # ── Change operations ─────────────────────────────────────────────────

    def checkout(self, branch: str) -> None:
        """Checkout *branch* in this working tree."""
        self._run_subprocess("checkout", branch)

    def reset_hard(self, ref: str = "HEAD") -> None:
        """Hard-reset to *ref*, discarding all uncommitted changes."""
        self._run_subprocess("reset", "--hard", ref)

    def merge_no_ff(self, branch: str, message: str) -> str:
        """Merge *branch* with ``--no-ff`` and commit message *message*.

        Returns the short SHA of the resulting merge commit.

        Raises
        ------
        MergeConflictError
            If the merge stops with conflicts.  The merge is left in
            progress so that a coder agent can resolve it.
        """
        result = self._run_subprocess("merge", "--no-ff", branch, "-m", message, check=False)
        if result.returncode != 0:
            raise MergeConflictError(branch, self.root, self.status_short())
        return self.rev_parse_short("HEAD")

    def merge_ff_only(self, branch: str) -> bool:
        """Fast-forward merge *branch* into the current branch.

        Returns ``True`` if HEAD advanced, ``False`` if already up to date.

        Raises
        ------
        UntrackedMergeBlockError
            If untracked files would be overwritten.
        subprocess.CalledProcessError
            On any other merge failure.
        """
        result = self._run_subprocess("merge", "--ff-only", branch, check=False)
        if result.returncode != 0:
            if "untracked working tree files would be overwritten" in result.stderr:
                raise UntrackedMergeBlockError(_parse_untracked_files(result.stderr))
            raise subprocess.CalledProcessError(
                result.returncode, result.args, result.stdout, result.stderr
            )
        return "Already up to date" not in result.stdout

    def rebase(self, base: str, autostash: bool = True) -> None:
        """Rebase the current branch on top of *base*.

        Raises
        ------
        RebaseConflictError
            If the rebase stops with conflicts.
        """
        args = ["rebase"]
        if autostash:
            args.append("--autostash")
        args.append(base)
        result = self._run_subprocess(*args, check=False)
        if result.returncode != 0:
            raise RebaseConflictError(self.root, self.status_short())
