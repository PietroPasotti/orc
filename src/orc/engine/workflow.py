"""orc – workflow routing and state machine helpers."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import structlog
import typer

import orc.engine.context as _ctx
import orc.git.core as _git
from orc.coordination.state import BoardStateManager
from orc.git.conflict import ConflictResolutionFailed, ConflictResolver
from orc.messaging import telegram as tg
from orc.squad import SquadConfig

logger = structlog.get_logger(__name__)


def _make_context_builder(
    squad_cfg: SquadConfig,
    board: BoardStateManager,
) -> Callable[[str, str, list[dict], Path | None], tuple[str, str]]:
    """Return a ``build_context`` callback that sources models from *squad_cfg*."""

    def _build(
        role: str,
        agent_id: str,
        messages: list[dict],
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

    def derive_task_state(self, task_name: str, task_data: dict | None = None) -> tuple[str, str]:
        return _git._derive_task_state(task_name, task_data)

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
        messages: list[dict],
        worktree: Path | None,
    ) -> tuple[str, str]:
        return self._build(role, agent_id, messages, worktree)

    def spawn(self, context: str, cwd: Path, model: str | None, log_path: Path | None) -> object:
        from orc.ai import invoke as inv

        return inv.spawn(context, cwd, model, log_path)

    def boot_message_body(self, agent_id: str) -> str:
        return _ctx._boot_message_body(agent_id, self._board)
