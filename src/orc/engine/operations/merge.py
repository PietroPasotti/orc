"""Merge operation — deterministic git merge with LLM conflict resolution.

Replaces the merger agent with:

1. **Deterministic merge** — ``git merge --no-ff`` (no LLM needed).
2. **Conflict resolution** — bounded agentic loop (max 20 iterations)
   with full file/shell tools.  The LLM gets conflict markers, both
   branches' intent (task description + commit messages), and resolves
   creatively.  This is genuine creative work — the LLM must understand
   both branches' intent to produce a correct merge.

The orchestrator drives the process: it starts the merge, detects conflicts,
sets up the LLM context with conflict details.  The LLM has real autonomy
within the conflict resolution loop.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import structlog

from orc.ai.llm import LLMClient
from orc.ai.runner import AgentRunner, RunnerConfig
from orc.ai.tools import ToolExecutor
from orc.git import Git, MergeConflictError
from orc.squad import AgentRole, PermissionConfig

logger = structlog.get_logger(__name__)

_MAX_CONFLICT_ITERATIONS = 20

# Permissions for conflict resolution: needs file read/write + git + shell
_CONFLICT_PERMISSIONS = PermissionConfig(
    mode="confined",
    allow_tools=(
        "read",
        "write",
        "shell(git:*)",
        "shell(just:*)",
    ),
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MergeResult:
    """Result of a merge operation."""

    success: bool
    """Whether the merge completed successfully."""

    merge_sha: str = ""
    """Short SHA of the merge commit (empty on failure)."""

    had_conflicts: bool = False
    """Whether merge conflicts were encountered."""

    message: str = ""
    """Human-readable summary of the merge outcome."""

    error: str = ""
    """Error message on failure."""


# ---------------------------------------------------------------------------
# Conflict resolution prompt
# ---------------------------------------------------------------------------

_CONFLICT_SYSTEM_PROMPT = """\
You are a merge conflict resolver.  A ``git merge --no-ff`` has left \
conflict markers in the working tree.  Your job is to resolve ALL \
conflicts so the merge can be completed.

You have access to file read/write tools and a shell for git commands.

## Strategy

1. Run ``git status --short`` to see which files have conflicts.
2. For each conflicted file, read it to understand both sides.
3. Resolve the conflict by editing the file — remove ALL conflict markers \
   (``<<<<<<<``, ``=======``, ``>>>>>>>``).
4. After resolving ALL files, run ``git add -A`` then \
   ``git merge --continue`` (or ``git commit --no-edit`` if merge is done).
5. Verify with ``git status`` that the working tree is clean.

## Rules

- Preserve the intent of BOTH branches.  When in doubt, keep both changes.
- NEVER just pick one side — merge the logic.
- After resolution, the code must be syntactically valid.
- Do NOT run tests — the orchestrator handles that separately.
"""


def _build_conflict_user_prompt(
    task_name: str,
    task_content: str,
    feature_branch: str,
    dev_branch: str,
    conflict_status: str,
    commit_log: str,
) -> str:
    """Build the user prompt for conflict resolution."""
    return f"""\
## Merge conflict

Merging ``{feature_branch}`` into ``{dev_branch}`` produced conflicts.

### Conflicted files

```
{conflict_status}
```

### Task being merged: {task_name}

{task_content}

### Recent commits on feature branch

```
{commit_log}
```

Resolve all conflicts now.
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_commit_log(git: Git, branch: str, base: str, max_commits: int = 20) -> str:
    """Return a compact commit log for *branch* since *base*."""
    try:
        result = git._run_subprocess(
            "log",
            "--oneline",
            f"--max-count={max_commits}",
            f"{base}..{branch}",
        )
        return str(result.stdout).strip() if result.stdout else "(no commits)"
    except Exception:
        return "(commit log unavailable)"


def _resolve_conflicts(
    *,
    task_name: str,
    task_content: str,
    feature_branch: str,
    dev_branch: str,
    dev_worktree: Path,
    conflict_status: str,
    commit_log: str,
    llm: LLMClient,
    log_fh: IO[str] | None = None,
    socket_path: str = "",
) -> bool:
    """Run a bounded agentic loop to resolve merge conflicts.

    Returns ``True`` if the merge was completed successfully.
    """
    system_prompt = _CONFLICT_SYSTEM_PROMPT
    user_prompt = _build_conflict_user_prompt(
        task_name,
        task_content,
        feature_branch,
        dev_branch,
        conflict_status,
        commit_log,
    )

    executor = ToolExecutor(
        cwd=dev_worktree,
        role=AgentRole.CODER,  # needs read+write+shell
        permissions=_CONFLICT_PERMISSIONS,
        socket_path=socket_path,
        agent_id=f"merge-{task_name}",
    )

    cancel = threading.Event()
    config = RunnerConfig(
        max_iterations=_MAX_CONFLICT_ITERATIONS,
        log_fh=log_fh,
        cancel_event=cancel,
    )
    runner = AgentRunner(llm, executor, config)
    exit_code = runner.run(system_prompt, user_prompt)

    if exit_code != 0:
        logger.warning(
            "conflict resolution loop ended non-zero",
            task=task_name,
            exit_code=exit_code,
        )

    # Check if the merge was actually completed
    git = Git(dev_worktree)
    if git.is_merge_in_progress():
        logger.error("conflict resolution did not complete the merge", task=task_name)
        git.merge_abort()
        return False

    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def merge_task(
    task_name: str,
    task_content: str,
    *,
    feature_branch: str,
    dev_branch: str,
    dev_worktree: Path,
    repo_root: Path,
    llm: LLMClient,
    log_fh: IO[str] | None = None,
    socket_path: str = "",
) -> MergeResult:
    """Merge a feature branch into dev with optional LLM conflict resolution.

    Parameters
    ----------
    task_name:
        Task filename (e.g. ``0046-eliminate-os-chdir.md``).
    task_content:
        Markdown content of the task file (provides context for conflicts).
    feature_branch:
        Name of the feature branch (e.g. ``feat/0046-eliminate-os-chdir``).
    dev_branch:
        Name of the dev branch.
    dev_worktree:
        Path to the dev worktree.
    repo_root:
        Path to the repository root.
    llm:
        LLM client for conflict resolution.
    log_fh:
        Optional log file handle.
    socket_path:
        ORC API socket path.
    """
    logger.info("merge_task: starting", task=task_name, feature=feature_branch)

    git_dev = Git(dev_worktree)
    git_root = Git(repo_root)

    # Reset dirty dev worktree
    if git_dev.is_dirty():
        logger.warning("merge_task: dev worktree dirty, resetting")
        git_dev.reset_hard()

    git_dev.checkout(dev_branch)

    # Attempt merge
    try:
        merge_sha = git_dev.merge_no_ff(feature_branch, f"Merge {feature_branch} into {dev_branch}")
        logger.info("merge_task: clean merge", task=task_name, sha=merge_sha)
        return MergeResult(
            success=True,
            merge_sha=merge_sha,
            had_conflicts=False,
            message=f"Clean merge of {feature_branch} ({merge_sha})",
        )
    except MergeConflictError as exc:
        logger.warning("merge_task: conflicts detected", task=task_name, status=exc.status_output)

    # Conflict path — resolve with LLM
    conflict_status = git_dev.status_short()
    commit_log = _get_commit_log(git_root, feature_branch, dev_branch)

    resolved = _resolve_conflicts(
        task_name=task_name,
        task_content=task_content,
        feature_branch=feature_branch,
        dev_branch=dev_branch,
        dev_worktree=dev_worktree,
        conflict_status=conflict_status,
        commit_log=commit_log,
        llm=llm,
        log_fh=log_fh,
        socket_path=socket_path,
    )

    if not resolved:
        return MergeResult(
            success=False,
            had_conflicts=True,
            message=f"Failed to resolve conflicts merging {feature_branch}",
            error="Conflict resolution loop did not complete the merge",
        )

    merge_sha = git_dev.rev_parse_short("HEAD")
    logger.info(
        "merge_task: conflicts resolved",
        task=task_name,
        sha=merge_sha,
    )
    return MergeResult(
        success=True,
        merge_sha=merge_sha,
        had_conflicts=True,
        message=f"Merged {feature_branch} with conflict resolution ({merge_sha})",
    )
