"""orc – workflow routing and state machine helpers."""

from __future__ import annotations

import re as _re
import subprocess
from collections.abc import Callable
from pathlib import Path

import structlog
import typer

import orc.config as _cfg
import orc.engine.context as _ctx
import orc.git.core as _git
from orc.git.conflict import ConflictResolutionFailed, ConflictResolver
from orc.messaging import telegram as tg
from orc.squad import AgentRole, SquadConfig

logger = structlog.get_logger(__name__)

_ORC_RESOLVED_RE = _re.compile(r"^\[orc\]\(resolved\)\s+\S+:\s+.*$")


def _has_unresolved_block(
    messages: list[dict],
) -> tuple[str, str] | tuple[None, None]:
    """Scan *messages* newest-to-oldest for an unresolved blocked/soft-blocked state."""
    blocked_states = {"blocked", "soft-blocked"}

    for msg in reversed(messages):
        text = msg.get("text", "").strip()

        if _ORC_RESOLVED_RE.match(text):
            return None, None

        m = tg._MSG_RE.match(text)
        if not m:
            continue
        name, state = m.group(1), m.group(2)
        role, _ = tg.parse_agent_id(name)
        if role is None:
            continue
        if state in tg.INFORMATIONAL_STATES:
            continue

        if state in blocked_states:
            return name, state

        return None, None

    return None, None


def _post_resolved(blocked_agent: str, blocked_state: str, resolver_agent: str) -> None:
    """Post an ``[orc](resolved)`` message to Telegram to close a blocked state."""
    body = f"{blocked_agent}({blocked_state}) addressed by {resolver_agent} invocation."
    tg.send_message(tg.format_agent_message("orc", "resolved", body))
    logger.info(
        "posted resolved message",
        blocked_agent=blocked_agent,
        blocked_state=blocked_state,
        resolver=resolver_agent,
    )


# TODO: each agent role should have a custom boot message
#  - planner: "Starting to refine vision NNNN-....md"
#  - coder-N: "Starting work on feat/NNNN-..."
#  - qa-N: "Starting to review feat/NNNN-..."
def _post_boot_message(agent_id: str, body: str) -> None:
    """Send ``[{agent_id}](boot) …`` to Telegram."""
    tg.send_message(tg.format_agent_message(agent_id, "boot", body))


def _do_close_board(task_name: str) -> None:
    """Crash-recovery: close *task_name* on board and commit via dev worktree."""
    dev_wt = _git._ensure_dev_worktree()
    logger.warning("crash recovery: closing board for merged branch", task=task_name)
    typer.echo(f"\n⟳ Crash recovery: closing board entry for {task_name}…")
    _git._close_task_on_board(task_name, dev_wt)
    try:
        config_rel = _cfg.get().orc_dir.relative_to(_cfg.get().repo_root)
    except ValueError:
        config_rel = Path(_cfg.get().orc_dir.name)
    board_path = dev_wt / config_rel / "work" / "board.yaml"
    if board_path.exists():
        subprocess.run(["git", "add", str(config_rel / "work")], cwd=dev_wt, check=True)
        subprocess.run(
            [
                "git",
                "commit",
                "-m",
                f"chore(orc): close task {Path(task_name).stem} (recovery)",
            ],
            cwd=dev_wt,
            check=True,
        )


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


def determine_next_agent(messages: list[dict]) -> tuple[str | None, str]:
    """Return ``(next_agent, reason)`` for the current workflow state."""
    blocked_agent, blocked_state = _has_unresolved_block(messages)
    if blocked_agent:
        if blocked_state == "soft-blocked":
            reason = f"{blocked_agent}(soft-blocked) — needs planner clarification"
            logger.info("unresolved soft-block, routing to planner", **{"from": blocked_agent})
            return AgentRole.PLANNER, reason
        reason = f"{blocked_agent}(blocked) — needs human intervention"
        logger.warning("unresolved hard block, stopping", agent=blocked_agent)
        return None, reason

    agent, reason = _git._derive_state_from_git()
    logger.info("git-derived state", next_agent=agent, reason=reason)

    if agent == AgentRole.PLANNER and not _ctx._has_planner_work():
        reason = "no vision docs, TODOs, or FIXMEs — nothing to plan"
        logger.info("skipping planner — nothing to plan")
        return None, reason

    return agent, reason


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
