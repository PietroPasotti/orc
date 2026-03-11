"""orc run command."""

from __future__ import annotations

import os
import sys
import time
from typing import Annotated

import structlog
import typer

import orc.board as _board
import orc.cli.merge as _merge_mod
import orc.cli.status as _status_mod
import orc.config as _cfg
import orc.context as _ctx
import orc.git as _git
import orc.workflow as _wf
from orc import dispatcher as _disp
from orc import invoke as inv
from orc import telegram as tg
from orc import tui as _tui
from orc.cli import _check_env_or_exit, app
from orc.pool import AgentProcess
from orc.squad import load_squad

logger = structlog.get_logger(__name__)

_DEV_AHEAD_REFRESH_INTERVAL = 30.0  # seconds between git queries


def _run(
    maxloops: int = 1, dry_run: bool = False, squad: str = "default", no_tui: bool = False
) -> None:
    _check_env_or_exit()

    squad_cfg = load_squad(squad, agents_dir=_cfg.AGENTS_DIR)
    logger.info(
        "orc run starting",
        maxloops=maxloops,
        dry_run=dry_run,
        squad=squad,
        coders=squad_cfg.coder,
        qa=squad_cfg.qa,
    )
    typer.echo("⟳ Syncing dev on main…")
    messages = tg.get_messages()
    _merge_mod._rebase_dev_on_main(messages, squad_cfg)

    _board.clear_all_assignments()

    use_tui = not no_tui and sys.stdout.isatty()

    state: _tui.RunState | None = None
    if use_tui:
        state = _tui.RunState(
            agents=[],
            dev_ahead=_safe_dev_ahead(),
            telegram_ok=bool(os.environ.get("COLONY_TELEGRAM_TOKEN")),
            backend=os.environ.get("COLONY_AI_CLI", "copilot"),
            current_loop=0,
            max_loops=maxloops,
        )

    _last_dev_refresh: list[float] = [0.0]

    def _on_agent_start(agent: AgentProcess) -> None:
        assert state is not None
        state.agents.append(
            _tui.AgentRow(
                agent_id=agent.agent_id,
                role=agent.role,
                model=agent.model,
                status="running",
                task_name=agent.task_name,
                worktree=str(agent.worktree),
                started_at=agent.started_at,
            )
        )

    def _on_agent_done(agent: AgentProcess, rc: int) -> None:
        assert state is not None
        state.agents = [r for r in state.agents if r.agent_id != agent.agent_id]

    callbacks = _disp.DispatchCallbacks(
        derive_task_state=_git._derive_task_state,
        get_open_tasks=_board.get_open_tasks,
        assign_task=_board.assign_task,
        unassign_task=_board.unassign_task,
        ensure_feature_worktree=_git._ensure_feature_worktree,
        ensure_dev_worktree=_git._ensure_dev_worktree,
        merge_feature=_wf._make_merge_feature_fn(squad_cfg),
        do_close_board=_wf._do_close_board,
        get_messages=tg.get_messages,
        has_unresolved_block=_wf._has_unresolved_block,
        wait_for_human_reply=_ctx.wait_for_human_reply,
        post_boot_message=_wf._post_boot_message,
        post_resolved=_wf._post_resolved,
        boot_message_body=_ctx._boot_message_body,
        build_context=_wf._make_context_builder(squad_cfg),
        spawn_fn=inv.spawn,
        get_pending_visions=_status_mod._pending_visions,
        get_pending_reviews=_status_mod._pending_reviews,
        on_agent_start=_on_agent_start if use_tui else None,
        on_agent_done=_on_agent_done if use_tui else None,
    )

    try:
        dispatcher = _disp.Dispatcher(squad_cfg, callbacks, dry_run=dry_run)
        if use_tui and state is not None:
            with _tui.live_context() as live:
                live.update(_tui.render(state))

                # Wrap dispatcher.run in a thread-free approach: we need to
                # call live.update() periodically while run() executes.
                # Since dispatcher.run() is synchronous, we hook into it by
                # wrapping get_messages to refresh the display on each poll.
                _orig_get_messages = callbacks.get_messages

                def _refreshing_get_messages() -> list[dict]:
                    assert state is not None
                    state.current_loop = dispatcher.loop
                    now = time.monotonic()
                    if now - _last_dev_refresh[0] >= _DEV_AHEAD_REFRESH_INTERVAL:
                        state.dev_ahead = _safe_dev_ahead()
                        _last_dev_refresh[0] = now
                    live.update(_tui.render(state))
                    return _orig_get_messages()

                callbacks.get_messages = _refreshing_get_messages
                dispatcher.run(maxloops=maxloops)
                live.update(_tui.render(state))
        else:
            dispatcher.run(maxloops=maxloops)
    except Exception:
        logger.exception("orc run loop crashed")
        raise


def _safe_dev_ahead() -> int:
    """Return dev-ahead-of-main count, or 0 on error."""
    try:
        return _status_mod._dev_ahead_of_main()
    except Exception:
        return 0


@app.command()
def run(
    maxloops: Annotated[
        int,
        typer.Option(
            help=(
                "Maximum dispatch cycles before stopping (0 = run until complete). "
                "One cycle may spawn a full squad of agents running in parallel."
            ),
        ),
    ] = 1,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the agent context/prompt without invoking."),
    ] = False,
    squad: Annotated[
        str,
        typer.Option(
            "--squad",
            help="Squad profile name (file in orc/squads/).  Default: 'default'.",
        ),
    ] = "default",
    no_tui: Annotated[
        bool,
        typer.Option("--no-tui", help="Disable the live TUI panel (use plain log output)."),
    ] = False,
) -> None:
    """Run the next dispatch cycle(s) of the workflow.

    One cycle may spawn a full squad of agents in parallel (e.g. a coder and a
    QA agent running concurrently).  Use ``--maxloops 0`` to run until the
    workflow completes or hard-blocks waiting for human input."""
    return _run(maxloops=maxloops, dry_run=dry_run, squad=squad, no_tui=no_tui)
