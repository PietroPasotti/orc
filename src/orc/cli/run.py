"""orc run command."""

from __future__ import annotations

from typing import Annotated

import structlog
import typer

import orc.board as _board
import orc.cli.merge as _merge_mod
import orc.config as _cfg
import orc.context as _ctx
import orc.git as _git
import orc.workflow as _wf
from orc import dispatcher as _disp
from orc import invoke as inv
from orc import telegram as tg
from orc.cli import _check_env_or_exit, app
from orc.squad import load_squad

logger = structlog.get_logger(__name__)


def _run(maxloops: int = 1, dry_run: bool = False, squad: str = "default") -> None:
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

    callbacks = _disp.DispatchCallbacks(
        derive_task_state=_git._derive_task_state,
        get_open_tasks=_board.get_open_tasks,
        assign_task=_board.assign_task,
        unassign_task=_board.unassign_task,
        ensure_feature_worktree=_git._ensure_feature_worktree,
        ensure_dev_worktree=_git._ensure_dev_worktree,
        merge_feature=_git._merge_feature_into_dev,
        do_close_board=_wf._do_close_board,
        get_messages=tg.get_messages,
        has_unresolved_block=_wf._has_unresolved_block,
        wait_for_human_reply=_ctx.wait_for_human_reply,
        post_boot_message=_wf._post_boot_message,
        post_resolved=_wf._post_resolved,
        boot_message_body=_ctx._boot_message_body,
        build_context=_wf._make_context_builder(squad_cfg),
        spawn_fn=inv.spawn,
    )

    try:
        dispatcher = _disp.Dispatcher(squad_cfg, callbacks, dry_run=dry_run)
        dispatcher.run(maxloops=maxloops)
    except Exception:
        logger.exception("orc run loop crashed")
        raise


@app.command()
def run(
    maxloops: Annotated[
        int,
        typer.Option(
            help=(
                "Maximum agent invocations before stopping. "
                "0 = run until the workflow completes or blocks."
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
) -> None:
    """Run the next agent(s) in the workflow."""
    return _run(maxloops=maxloops, dry_run=dry_run, squad=squad)
