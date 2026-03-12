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
from orc.messaging import telegram as tg
from orc.squad import SquadConfig

logger = structlog.get_logger(__name__)

KNOWN_AGENTS = tg.KNOWN_ROLES

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
        config_rel = _cfg.AGENTS_DIR.relative_to(_cfg.REPO_ROOT)
    except ValueError:
        config_rel = Path(_cfg.AGENTS_DIR.name)
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
            return "planner", reason
        reason = f"{blocked_agent}(blocked) — needs human intervention"
        logger.warning("unresolved hard block, stopping", agent=blocked_agent)
        return None, reason

    agent, reason = _git._derive_state_from_git()
    logger.info("git-derived state", next_agent=agent, reason=reason)

    if agent == "planner" and not _ctx._has_planner_work():
        reason = "no vision docs, TODOs, or FIXMEs — nothing to plan"
        logger.info("skipping planner — nothing to plan")
        return None, reason

    return agent, reason


def _make_merge_feature_fn(squad_cfg: SquadConfig) -> Callable[[str], None]:
    """Return a ``merge_feature`` callback with automatic conflict resolution.

    On a clean merge, the returned function behaves identically to
    :func:`orc.git._merge_feature_into_dev`.

    When the merge stops with conflicts (:class:`~orc.git.MergeConflictError`),
    a coder agent is spawned to resolve the conflict markers and complete the
    merge with ``git merge --continue``.  If the coder fails or exits without
    finishing the merge, a :class:`typer.Exit` is raised.
    """

    def _merge(task_name: str) -> None:
        try:
            _git._merge_feature_into_dev(task_name)
        except _git.MergeConflictError as exc:
            branch = exc.branch
            dev_wt = exc.worktree
            status_output = exc.status_output

            typer.echo(
                f"⚠ Merge conflict on {branch!r}:\n{status_output}\nDelegating to coder agent…"
            )

            conflict_extra = (
                f"## Feature merge conflict — your task\n\n"
                f"A `git merge --no-ff {branch}` into `{_cfg.WORK_DEV_BRANCH}` was attempted "
                f"and stopped with conflicts.  The merge is currently paused in the dev "
                f"worktree at `{dev_wt}`.\n\n"
                f"Conflicting files (from `git status --short`):\n```\n{status_output}\n```\n\n"
                "**What you must do:**\n"
                "1. Open each conflicting file, resolve the conflict markers "
                "(`<<<<<<<`, `=======`, `>>>>>>>`).\n"
                "2. `git add <resolved-file>` for each resolved file.\n"
                "3. `git merge --continue` to complete the merge.\n"
                "4. Do NOT `git merge --abort`. Finish the merge.\n"
                "5. Exit when the merge is complete.\n"
            )

            messages = tg.get_messages()
            coder_model = squad_cfg.model("coder")
            model, context = _ctx.build_agent_context(
                "coder",
                messages,
                extra=conflict_extra,
                worktree=dev_wt,
                model=coder_model,
            )
            rc = _ctx.invoke_agent("coder", context, model)

            if rc != 0:
                logger.error("coder agent failed to resolve merge conflict", exit_code=rc)
                typer.echo(f"✗ Coder agent exited with code {rc} while resolving merge conflict.")
                raise typer.Exit(code=rc)

            if _git._merge_in_progress(dev_wt):
                logger.error("merge still in progress after coder exited", branch=branch)
                typer.echo(
                    "✗ Merge still in progress after agent exit.  Manual intervention needed."
                )
                raise typer.Exit(code=1)

            logger.info("merge conflict resolved by coder agent", branch=branch)
            typer.echo(f"✓ Merge conflict on {branch!r} resolved by coder agent.")

    return _merge
