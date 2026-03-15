"""orc – workflow routing and state machine helpers."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import structlog
import typer

import orc.engine.context as _ctx
import orc.git.core as _git
from orc.git.conflict import ConflictResolutionFailed, ConflictResolver
from orc.messaging import telegram as tg
from orc.squad import SquadConfig

logger = structlog.get_logger(__name__)


def _post_boot_message(agent_id: str) -> None:
    """Build and send ``[{agent_id}](boot) …`` to Telegram."""
    body = _ctx._boot_message_body(agent_id)
    tg.send_message(tg.format_agent_message(agent_id, "boot", body))


def _do_close_board(task_name: str) -> None:
    """Crash-recovery: close *task_name* on the board (cache write, no git commit)."""
    logger.warning("crash recovery: closing board for merged branch", task=task_name)
    typer.echo(f"\n⟳ Crash recovery: closing board entry for {task_name}…")
    _git._close_task_on_board(task_name)


def _make_context_builder(
    squad_cfg: SquadConfig,
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

    def derive_task_state(self, task_name: str) -> tuple[str, str]:
        return _git._derive_task_state(task_name)

    def merge_feature(self, task_name: str) -> None:
        self._merge(task_name)

    def do_close_board(self, task_name: str) -> None:
        _do_close_board(task_name)


class AgentSvc:
    """Bundles agent-spawn callbacks that require *squad_cfg* at construction time."""

    def __init__(self, squad_cfg: SquadConfig) -> None:
        self._build = _make_context_builder(squad_cfg)

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
