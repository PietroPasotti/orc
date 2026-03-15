"""orc – workflow routing, git orchestration, and conflict resolution."""

from __future__ import annotations

import re
import typing
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import structlog
import typer

import orc.config as _cfg
import orc.engine.context as _ctx
from orc.ai.backends import SpawnResult
from orc.coordination.board import TaskStatus
from orc.coordination.models import TaskEntry
from orc.coordination.state import BoardStateManager
from orc.engine.state_machine import ACTION_CLOSE_BOARD as _CLOSE_BOARD
from orc.engine.state_machine import LastCommit, WorldState
from orc.engine.state_machine import route as _route
from orc.git import Git, MergeConflictError, RebaseConflictError
from orc.squad import AgentRole, SquadConfig

logger = structlog.get_logger(__name__)

# Map board task status → LastCommit enum for state machine routing.
_STATUS_TO_LAST_COMMIT: dict[str, LastCommit] = {
    TaskStatus.PLANNED: LastCommit.CODER_WORK,
    TaskStatus.IN_PROGRESS: LastCommit.CODER_WORK,
    TaskStatus.IN_REVIEW: LastCommit.CODER_DONE,
    TaskStatus.DONE: LastCommit.QA_PASSED,
    TaskStatus.BLOCKED: LastCommit.CODER_WORK,
    TaskStatus.STUCK: LastCommit.CODER_WORK,
}


# ---------------------------------------------------------------------------
# Conflict resolution
# ---------------------------------------------------------------------------


class ConflictResolutionFailed(Exception):
    """Raised when conflict resolution by a coder agent fails.

    CLI callers should convert this to :class:`~typer.Exit`.

    Attributes
    ----------
    code:
        Suggested process exit code.
    """

    def __init__(self, code: int = 1) -> None:
        super().__init__(f"Conflict resolution failed (exit code {code})")
        self.code = code


class ConflictResolver:
    """Resolves git merge and rebase conflicts by delegating to a coder agent.

    Parameters
    ----------
    squad_cfg:
        Squad configuration; used to select the coder model.
    """

    def __init__(self, squad_cfg: SquadConfig) -> None:
        self.squad_cfg = squad_cfg

    def _coder_model(self) -> str:
        return self.squad_cfg.model(AgentRole.CODER)

    _MERGE_RESOLVER_CONTEXT = """
    ## Feature merge conflict — your task\n
    We need to merge `{source_branch}` into `{target_branch}`, but there are conflicts.  
    Your task is to resolve the merge conflicts and complete the merge.
    """
    _REBASE_RESOLVER_CONTEXT = """
    ## Startup rebase conflict — your task\n
    A `git rebase {source_branch}` of `{target_branch}` was attempted and stopped with conflicts.
    Your task is to resolve the rebase conflicts and complete the rebase.
    """

    def _merge_with_conflicts(
        self,
        branch: str,
        target: str,
        worktree: Path,
        reason: typing.Literal["rebase"] | typing.Literal["merge"],
    ) -> None:
        board = BoardStateManager(_cfg.get().orc_dir)
        context = _ctx.build_agent_context(AgentRole.CODER, board, "merger-0", plain=True)
        match reason:
            case "rebase":
                template = self._REBASE_RESOLVER_CONTEXT
            case "merge":
                template = self._MERGE_RESOLVER_CONTEXT
            case _:  # pragma: no cover
                typing.assert_never(reason)
        context += template.format(source_branch=branch, target_branch=target)
        rc = _ctx.invoke_agent(AgentRole.CODER, context, self._coder_model(), worktree=worktree)

        if rc != 0:
            logger.error(f"coder agent failed to resolve {reason} conflict", exit_code=rc)
            typer.echo(f"✗ Coder agent exited with code {rc} while resolving {reason} conflict.")
            raise ConflictResolutionFailed(code=rc)
        match reason:
            case "rebase":
                if Git(worktree).is_rebase_in_progress():
                    logger.error("rebase still in progress after coder exited", branch=branch)
                    typer.echo(
                        "✗ Rebase still in progress after agent exit.  Manual intervention needed."
                    )
                    raise ConflictResolutionFailed(code=1)
            case "merge":
                if Git(worktree).is_merge_in_progress():
                    logger.error("merge still in progress after coder exited", branch=branch)
                    typer.echo(
                        "✗ Merge still in progress after agent exit.  Manual intervention needed."
                    )
                    raise ConflictResolutionFailed(code=1)
            case _:  # pragma: no cover
                typing.assert_never(reason)
        logger.info("conflict resolved by agent", branch=branch)

    def resolve_merge_conflict(self, branch: str, worktree: Path, status_output: str) -> None:
        """Delegate resolution of a paused ``git merge --no-ff`` to a coder agent.

        Raises
        ------
        ConflictResolutionFailed
            If the agent exits non-zero or the merge is still in progress
            after the agent exits.
        """
        typer.echo(f"⚠ Merge conflict on {branch!r}:\n{status_output}\nDelegating to coder agent…")
        self._merge_with_conflicts(branch, _cfg.get().work_dev_branch, worktree, reason="merge")
        logger.info(f"Merge conflict on {branch!r} resolved by coder agent.")
        typer.echo(f"✓ Merge conflict on {branch!r} resolved by coder agent.")

    def resolve_rebase_conflict(self, worktree: Path, status_output: str) -> None:
        """Delegate resolution of a paused ``git rebase`` to a coder agent.

        Raises
        ------
        ConflictResolutionFailed
            If the agent exits non-zero or the rebase is still in progress
            after the agent exits.
        """
        cfg = _cfg.get()
        typer.echo(f"⚠ Startup rebase conflict:\n{status_output}\nDelegating to coder agent…")
        self._merge_with_conflicts(cfg.main_branch, cfg.work_dev_branch, worktree, reason="rebase")
        logger.info("dev rebased on main after conflict resolution by coder")
        typer.echo("✓ dev rebased on main (conflicts resolved by coder).")


def features_in_dev_not_main() -> list[str]:
    """Return orc feature branches merged into dev but not yet into main.

    Parses merge-commit subjects from ``git log --merges main..<dev>``,
    matching the format: ``Merge [<prefix>/]feat/<slug> into <dev-branch>``.
    Works even after local branches have been deleted.
    """
    cfg = _cfg.get()
    git = Git(cfg.repo_root)
    lines = git.log_merges_oneline(f"{cfg.main_branch}..{cfg.work_dev_branch}")
    prefix_pat = re.escape(cfg.branch_prefix + "/") if cfg.branch_prefix else ""
    pat = re.compile(rf"Merge ({prefix_pat}feat/\S+) into \S+")
    branches: list[str] = []
    for line in lines:
        m = pat.search(line)
        if m:
            branches.append(m.group(1))
    return branches


def _append_changelog_entry(task_name: str, branch: str, merge_sha: str, orc_dir: Path) -> None:
    """Append a merge entry to ``orc-CHANGELOG.md`` in *orc_dir*."""
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    task_stem = Path(task_name).stem
    entry = (
        f"\n## {task_stem} (merged {timestamp})\n\n"
        f"**Branch:** {branch}\n\n"
        f"**Task:** {task_name}\n\n"
        f"**Merge commit:** {merge_sha}\n"
    )
    changelog = orc_dir / "orc-CHANGELOG.md"
    if changelog.exists():
        changelog.write_text(changelog.read_text() + entry)
    else:
        changelog.write_text(f"# Changelog\n{entry}")
    logger.info("changelog updated", task=task_name, merge_sha=merge_sha)


def _merge_feature_into_dev(task_name: str) -> None:
    """Merge the feature branch for *task_name* into dev and clean up.

    If the dev worktree has uncommitted changes they are discarded with a
    hard reset before merging.  Raises :class:`~orc.git.MergeConflictError`
    on merge conflicts so the caller can delegate resolution to a coder agent.
    """
    cfg = _cfg.get()
    branch = cfg.feature_branch(task_name)
    wt_path = cfg.feature_worktree_path(task_name)
    git_root = Git(cfg.repo_root)

    git_root.ensure_worktree(cfg.dev_worktree, cfg.work_dev_branch)
    git_dev = Git(cfg.dev_worktree)

    if git_dev.is_dirty():
        logger.warning(
            "dev worktree is dirty before merge — resetting to HEAD",
            worktree=str(cfg.dev_worktree),
            branch=branch,
        )
        git_dev.reset_hard()

    logger.info("merging feature into dev", feature_branch=branch, dev_branch=cfg.work_dev_branch)
    git_dev.checkout(cfg.work_dev_branch)
    merge_sha = git_dev.merge_no_ff(branch, f"Merge {branch} into {cfg.work_dev_branch}")

    _append_changelog_entry(task_name, branch, merge_sha, cfg.orc_dir)
    logger.info("feature merged", task=task_name, commit_tag=merge_sha)

    if wt_path.exists():
        logger.info("removing feature worktree", path=str(wt_path))
        git_root.worktree_remove(wt_path)

    git_root.worktree_prune()
    git_root.branch_delete(branch, force=True)


def rebase_dev_on_main(squad_cfg: SquadConfig | None = None) -> None:
    """Rebase dev on top of main so each session starts with the latest code.

    If the rebase produces conflicts a coder agent is invoked to resolve them
    via :class:`ConflictResolver`.  On resolution failure
    :class:`~typer.Exit` is raised.
    """
    cfg = _cfg.get()
    git_root = Git(cfg.repo_root)
    git_root.ensure_worktree(cfg.dev_worktree, cfg.work_dev_branch)
    git_dev = Git(cfg.dev_worktree)
    try:
        git_dev.rebase(cfg.main_branch)
        typer.echo("✓ dev rebased on main.")
    except RebaseConflictError as exc:
        _squad = squad_cfg
        if _squad is None:
            # Use a minimal stand-in so ConflictResolver can pick the default model
            class _DefaultModel:
                def model(self, role: str) -> str:
                    return _ctx._DEFAULT_MODEL

            _squad = _DefaultModel()  # type: ignore[assignment]
        resolver = ConflictResolver(squad_cfg=_squad)  # type: ignore[arg-type]
        try:
            resolver.resolve_rebase_conflict(cfg.dev_worktree, exc.status_output)
        except ConflictResolutionFailed as e:
            raise typer.Exit(code=e.code) from e


# ---------------------------------------------------------------------------
# Worktree management (config-aware, satisfies WorktreeService protocol)
# ---------------------------------------------------------------------------


class WorktreeManager:
    """Manages feature and dev worktrees using the current config.

    Satisfies :class:`~orc.engine.services.WorktreeService`.
    """

    def ensure_feature_worktree(self, task_name: str) -> Path:
        """Ensure the feature branch and worktree for *task_name* exist."""
        cfg = _cfg.get()
        git = Git(cfg.repo_root)
        wt_path = cfg.feature_worktree_path(task_name)
        branch = cfg.feature_branch(task_name)
        git.ensure_worktree(wt_path, branch, from_branch=git.default_branch())
        return wt_path

    def ensure_dev_worktree(self) -> Path:
        """Ensure the dev branch and worktree exist."""
        cfg = _cfg.get()
        Git(cfg.repo_root).ensure_worktree(cfg.dev_worktree, cfg.work_dev_branch)
        return cfg.dev_worktree


# ---------------------------------------------------------------------------
# State derivation
# ---------------------------------------------------------------------------


def _derive_task_state(task_name: str, task_data: TaskEntry | None = None) -> tuple[str, str]:
    """Inspect the git tree and *task_data* for *task_name* and return ``(token, reason)``.

    Git branch checks determine whether work has started and completed.
    Routing is delegated to :func:`~orc.engine.state_machine.route` —
    the single source of truth.  *task_data* is the task's board entry dict;
    when ``None``, defaults to treating the task as ``in-progress``.
    """
    cfg = _cfg.get()
    branch = cfg.feature_branch(task_name)

    branch_exists = Git(_cfg.get().repo_root).branch_exists(branch)
    logger.debug(
        "derive_task_state: branch exists", task=task_name, branch=branch, exists=branch_exists
    )

    if not branch_exists:
        return AgentRole.CODER, f"feature branch {branch!r} does not exist yet"

    has_commits = Git(cfg.repo_root).has_commits_ahead_of(branch, cfg.main_branch)
    if not has_commits:
        if Git(cfg.repo_root).is_merged_into(branch, cfg.work_dev_branch):
            logger.info(
                "derive_task_state: already merged into dev — closing board",
                task=task_name,
                branch=branch,
            )
            return _CLOSE_BOARD, f"branch {branch!r} already merged into dev but board not updated"
        return AgentRole.CODER, f"feature branch {branch!r} has no commits ahead of main"

    status = (task_data.status if task_data else None) or TaskStatus.IN_PROGRESS
    last_commit = _STATUS_TO_LAST_COMMIT.get(status, LastCommit.CODER_WORK)
    logger.debug(
        "derive_task_state: board status", task=task_name, status=status, last_commit=last_commit
    )

    world_state = WorldState(
        has_open_task=True, branch_exists=True, commits_ahead=True, last_commit=last_commit
    )
    action = _route(world_state)

    _REASONS = {
        TaskStatus.IN_REVIEW: f"coder finished {branch!r}, awaiting QA",
        TaskStatus.DONE: f"qa approved {branch!r} — ready to merge",
    }
    try:
        ts = TaskStatus(status) if status else None
    except ValueError:
        ts = None
    fallback = f"{branch!r} status={status!r}"
    reason = _REASONS.get(ts, fallback) if ts else fallback
    return action, reason  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Service objects
# ---------------------------------------------------------------------------


class _ContextBuilder(Protocol):
    def __call__(
        self,
        role: AgentRole,
        agent_id: str,
        task_name: str | None = ...,
    ) -> tuple[str, str]: ...


def _make_context_builder(
    squad_cfg: SquadConfig,
    board: BoardStateManager,
) -> _ContextBuilder:
    """Return a ``build_context`` callback that sources models from *squad_cfg*."""

    def _build(
        role: AgentRole,
        agent_id: str,
        task_name: str | None = None,
    ) -> tuple[str, str]:
        return (
            squad_cfg.model(role),
            _ctx.build_agent_context(role, board=board, agent_id=agent_id, task_name=task_name),
        )

    return _build


def _make_merge_feature_fn(squad_cfg: SquadConfig) -> Callable[[str], None]:
    """Return a ``merge_feature`` callback with automatic conflict resolution."""

    def _merge(task_name: str) -> None:
        try:
            _merge_feature_into_dev(task_name)
        except MergeConflictError as exc:
            resolver = ConflictResolver(squad_cfg=squad_cfg)
            try:
                resolver.resolve_merge_conflict(exc.branch, exc.worktree, exc.status_output)
            except ConflictResolutionFailed as e:
                raise typer.Exit(code=e.code) from e

    return _merge


class WorkflowSvc:
    """Bundles workflow callbacks that require *squad_cfg* at construction time."""

    def __init__(self, squad_cfg: SquadConfig) -> None:
        self._merge = _make_merge_feature_fn(squad_cfg)

    def derive_task_state(
        self, task_name: str, task_data: TaskEntry | None = None
    ) -> tuple[str, str]:
        return _derive_task_state(task_name, task_data)

    def merge_feature(self, task_name: str) -> None:
        self._merge(task_name)


class AgentSvc:
    """Bundles agent-spawn callbacks that require *squad_cfg* at construction time."""

    def __init__(self, squad_cfg: SquadConfig, board: BoardStateManager) -> None:
        self._build: _ContextBuilder = _make_context_builder(squad_cfg, board)
        self._board = board
        self._squad_cfg = squad_cfg

    def build_context(
        self,
        role: AgentRole,
        agent_id: str,
        task_name: str | None = None,
    ) -> tuple[str, str]:
        return self._build(role, agent_id, task_name=task_name)

    def spawn(
        self,
        context: str,
        cwd: Path,
        model: str | None,
        log_path: Path | None,
        agent_id: str | None = None,
        role: AgentRole | None = None,
    ) -> SpawnResult:
        from orc.ai import invoke as inv

        permissions = self._squad_cfg.permissions(role) if role is not None else None
        return inv.spawn(
            context,
            cwd,
            model,
            log_path,
            agent_id=agent_id,
            role=str(role) if role is not None else None,
            permissions=permissions,
        )

    def boot_message_body(self, agent_id: str) -> str:
        return _ctx._boot_message_body(agent_id, self._board)
