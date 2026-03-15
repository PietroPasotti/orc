"""orc – workflow routing, git orchestration, and conflict resolution."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

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
from orc.messaging import telegram as tg
from orc.messaging.messages import ChatMessage
from orc.squad import AgentRole, SquadConfig

logger = structlog.get_logger(__name__)

# Map board task status → LastCommit enum for state machine routing.
_STATUS_TO_LAST_COMMIT: dict[str, LastCommit] = {
    TaskStatus.PLANNED: LastCommit.CODER_WORK,
    TaskStatus.IN_PROGRESS: LastCommit.CODER_WORK,
    TaskStatus.IN_REVIEW: LastCommit.CODER_DONE,
    TaskStatus.DONE: LastCommit.QA_PASSED,
    TaskStatus.BLOCKED: LastCommit.CODER_WORK,
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
    messages:
        Current Telegram message history; passed as context to the agent.
    """

    def __init__(self, squad_cfg: SquadConfig, messages: list[ChatMessage]) -> None:
        self.squad_cfg = squad_cfg
        self.messages = messages

    def _coder_model(self) -> str:
        return self.squad_cfg.model(AgentRole.CODER)

    def resolve_merge_conflict(self, branch: str, worktree: Path, status_output: str) -> None:
        """Delegate resolution of a paused ``git merge --no-ff`` to a coder agent.

        Raises
        ------
        ConflictResolutionFailed
            If the agent exits non-zero or the merge is still in progress
            after the agent exits.
        """
        cfg = _cfg.get()
        typer.echo(f"⚠ Merge conflict on {branch!r}:\n{status_output}\nDelegating to coder agent…")
        conflict_extra = (
            f"## Feature merge conflict — your task\n\n"
            f"A `git merge --no-ff {branch}` into `{cfg.work_dev_branch}` was attempted "
            f"and stopped with conflicts.  The merge is currently paused in the dev "
            f"worktree at `{worktree}`.\n\n"
            f"Conflicting files (from `git status --short`):\n```\n{status_output}\n```\n\n"
            "**What you must do:**\n"
            "1. Open each conflicting file, resolve the conflict markers "
            "(`<<<<<<<`, `=======`, `>>>>>>>`).\n"
            "2. `git add <resolved-file>` for each resolved file.\n"
            "3. `git merge --continue` to complete the merge.\n"
            "4. Do NOT `git merge --abort`. Finish the merge.\n"
            "5. Exit when the merge is complete.\n"
        )
        model, context = _ctx.build_agent_context(
            AgentRole.CODER,
            self.messages,
            extra=conflict_extra,
            worktree=worktree,
            model=self._coder_model(),
        )
        rc = _ctx.invoke_agent(AgentRole.CODER, context, model)
        if rc != 0:
            logger.error("coder agent failed to resolve merge conflict", exit_code=rc)
            typer.echo(f"✗ Coder agent exited with code {rc} while resolving merge conflict.")
            raise ConflictResolutionFailed(code=rc)
        if Git(worktree).is_merge_in_progress():
            logger.error("merge still in progress after coder exited", branch=branch)
            typer.echo("✗ Merge still in progress after agent exit.  Manual intervention needed.")
            raise ConflictResolutionFailed(code=1)
        logger.info("merge conflict resolved by coder agent", branch=branch)
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
        conflict_extra = (
            "## Startup rebase conflict — your task\n\n"
            f"A `git rebase {cfg.main_branch}` of the `{cfg.work_dev_branch}` "
            "branch was attempted at session start and stopped with conflicts.  "
            "The rebase is currently paused in the dev worktree.\n\n"
            f"Conflicting files (from `git status --short`):\n```\n{status_output}\n```\n\n"
            "**What you must do:**\n"
            "1. Open each conflicting file, resolve the conflict markers (`<<<<<<<`, "
            "`=======`, `>>>>>>>`).\n"
            "2. `git add <resolved-file>` for each resolved file.\n"
            "3. `git rebase --continue` (repeat steps 1–3 if git stops again).\n"
            "4. Do NOT `git rebase --abort`. Finish the rebase.\n"
            "5. Exit when the rebase is complete.\n"
        )
        coder_model = self._coder_model()
        model, context = _ctx.build_agent_context(
            AgentRole.CODER, self.messages, extra=conflict_extra, model=coder_model
        )
        rc = _ctx.invoke_agent(AgentRole.CODER, context, model)
        if rc != 0:
            logger.error("coder agent failed to resolve startup rebase", exit_code=rc)
            typer.echo(f"✗ Coder agent exited with code {rc} while resolving startup rebase.")
            raise ConflictResolutionFailed(code=rc)
        if Git(worktree).is_rebase_in_progress():
            logger.error("rebase still in progress after coder exited")
            typer.echo("✗ Rebase still in progress after agent exit. Manual intervention needed.")
            raise ConflictResolutionFailed(code=1)
        logger.info("dev rebased on main after conflict resolution by coder")
        typer.echo("✓ dev rebased on main (conflicts resolved by coder).")


# ---------------------------------------------------------------------------
# Git orchestration helpers
# ---------------------------------------------------------------------------


def _feature_branch_exists(branch: str) -> bool:
    """Return True if *branch* exists locally."""
    return Git(_cfg.get().repo_root).branch_exists(branch)


def _feature_has_commits_ahead_of_main(branch: str) -> bool:
    """Return True if *branch* has at least one commit not in the main branch."""
    cfg = _cfg.get()
    return Git(cfg.repo_root).has_commits_ahead_of(branch, cfg.main_branch)


def _feature_merged_into_dev(branch: str) -> bool:
    """Return True if *branch* has been merged into the dev branch."""
    cfg = _cfg.get()
    return Git(cfg.repo_root).is_merged_into(branch, cfg.work_dev_branch)


def _features_in_dev_not_main() -> list[str]:
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


def _count_features_done() -> int:
    """Count orc feature branches merged into dev but not yet on main."""
    return len(_features_in_dev_not_main())


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


def rebase_dev_on_main(messages: list[ChatMessage], squad_cfg: SquadConfig | None = None) -> None:
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
        resolver = ConflictResolver(squad_cfg=_squad, messages=messages)  # type: ignore[arg-type]
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


def _make_context_builder(
    squad_cfg: SquadConfig,
    board: BoardStateManager,
) -> Callable[[str, str, list[ChatMessage], Path | None], tuple[str, str]]:
    """Return a ``build_context`` callback that sources models from *squad_cfg*."""

    def _build(
        role: str,
        agent_id: str,
        messages: list[ChatMessage],
        worktree: Path | None,
    ) -> tuple[str, str]:
        return _ctx.build_agent_context(
            role,
            messages,
            board,
            worktree=worktree,
            agent_id=agent_id,
            model=squad_cfg.model(role),
        )

    return _build


def _make_merge_feature_fn(squad_cfg: SquadConfig) -> Callable[[str], None]:
    """Return a ``merge_feature`` callback with automatic conflict resolution."""

    def _merge(task_name: str) -> None:
        try:
            _merge_feature_into_dev(task_name)
        except MergeConflictError as exc:
            messages = tg.TelegramMessagingService().get_messages()
            resolver = ConflictResolver(squad_cfg=squad_cfg, messages=messages)
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
        self._build = _make_context_builder(squad_cfg, board)
        self._board = board

    def build_context(
        self,
        role: str,
        agent_id: str,
        messages: list[ChatMessage],
        worktree: Path | None,
    ) -> tuple[str, str]:
        return self._build(role, agent_id, messages, worktree)

    def spawn(
        self, context: str, cwd: Path, model: str | None, log_path: Path | None
    ) -> SpawnResult:
        from orc.ai import invoke as inv

        return inv.spawn(context, cwd, model, log_path)

    def boot_message_body(self, agent_id: str) -> str:
        return _ctx._boot_message_body(agent_id, self._board)
