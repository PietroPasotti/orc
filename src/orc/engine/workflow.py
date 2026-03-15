"""orc – workflow routing and state machine helpers."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import structlog
import typer

import orc.engine.context as _ctx
import orc.git.core as _git
from orc.ai.backends import SpawnResult
from orc.coordination.board import TaskStatus
from orc.coordination.models import TaskEntry
from orc.coordination.state import BoardStateManager
from orc.engine.state_machine import ACTION_CLOSE_BOARD as _CLOSE_BOARD
from orc.engine.state_machine import LastCommit, WorldState
from orc.engine.state_machine import route as _route
from orc.git.conflict import ConflictResolutionFailed, ConflictResolver
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


def _derive_task_state(task_name: str, task_data: TaskEntry | None = None) -> tuple[str, str]:
    """Inspect the git tree and *task_data* for *task_name* and return ``(token, reason)``.

    Git branch checks determine whether work has started and completed.
    Routing is delegated to :func:`~orc.engine.state_machine.route` —
    the single source of truth.  *task_data* is the task's board entry dict;
    when ``None``, defaults to treating the task as ``in-progress``.
    """
    branch = _git._feature_branch(task_name)

    branch_exists = _git._feature_branch_exists(branch)
    logger.debug(
        "derive_task_state: branch exists", task=task_name, branch=branch, exists=branch_exists
    )

    if not branch_exists:
        return AgentRole.CODER, f"feature branch {branch!r} does not exist yet"

    has_commits = _git._feature_has_commits_ahead_of_main(branch)
    if not has_commits:
        if _git._feature_merged_into_dev(branch):
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
    """Return a ``merge_feature`` callback with automatic conflict resolution.

    On a clean merge, the returned function behaves identically to
    :func:`orc.git._merge_feature_into_dev`.

    When the merge stops with conflicts (:class:`~orc.git.MergeConflictError`),
    a :class:`~orc.git.conflict.ConflictResolver` is used to delegate the
    conflict resolution to a coder agent.  If resolution fails,
    :class:`~typer.Exit` is raised.
    """

    def _merge(task_name: str) -> None:
        try:
            _git._merge_feature_into_dev(task_name)
        except _git.MergeConflictError as exc:
            messages = tg.get_messages()
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
