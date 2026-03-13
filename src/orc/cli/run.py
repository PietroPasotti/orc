"""orc run command."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Annotated

import structlog
import typer

import orc.board as _board
import orc.cli.merge as _merge_mod
import orc.cli.status as _status_mod
import orc.config as _cfg
import orc.engine.context as _ctx
import orc.engine.workflow as _wf
import orc.git.core as _git
from orc import tui as _tui
from orc.ai import invoke as inv
from orc.cli import _check_env_or_exit, app
from orc.engine import dispatcher as _disp
from orc.engine.pool import AgentProcess
from orc.engine.work import Work
from orc.messaging import telegram as tg
from orc.squad import load_squad

_MAXCALLS_UNLIMITED = sys.maxsize

logger = structlog.get_logger(__name__)

_DEV_AHEAD_REFRESH_INTERVAL = 30.0  # seconds between git queries


# ---------------------------------------------------------------------------
# Service adapters — satisfy the Protocol contracts using module functions
# ---------------------------------------------------------------------------


class _BoardSvc:
    def get_open_tasks(self) -> list[dict]:
        return _board.get_open_tasks()

    def assign_task(self, task_name: str, agent_id: str) -> None:
        _board.assign_task(task_name, agent_id)

    def unassign_task(self, task_name: str) -> None:
        _board.unassign_task(task_name)

    def get_pending_visions(self) -> list[str]:
        return _status_mod._pending_visions()

    def get_pending_reviews(self) -> list[str]:
        return _status_mod._pending_reviews()

    def scan_todos(self) -> list[dict]:
        return _ctx._scan_todos(_cfg.get().repo_root)


class _WorktreeSvc:
    def ensure_feature_worktree(self, task_name: str) -> Path:
        return _git._ensure_feature_worktree(task_name)

    def ensure_dev_worktree(self) -> Path:
        return _git._ensure_dev_worktree()


class _MessagingSvc:
    def get_messages(self) -> list[dict]:
        return tg.get_messages()

    def has_unresolved_block(self, messages: list[dict]) -> tuple[str | None, str | None]:
        return _wf._has_unresolved_block(messages)

    def wait_for_human_reply(self, messages: list[dict]) -> str:
        return _ctx.wait_for_human_reply(messages)

    def post_boot_message(self, agent_id: str, body: str) -> None:
        _wf._post_boot_message(agent_id, body)

    def post_resolved(self, blocked_agent: str, blocked_state: str, resolver: str) -> None:
        _wf._post_resolved(blocked_agent, blocked_state, resolver)

    def boot_message_body(self) -> str:
        return _ctx._boot_message_body()


class _WorkflowSvc:
    def __init__(self, squad_cfg) -> None:
        self._merge = _wf._make_merge_feature_fn(squad_cfg)

    def derive_task_state(self, task_name: str) -> tuple[str, str]:
        return _git._derive_task_state(task_name)

    def merge_feature(self, task_name: str) -> None:
        self._merge(task_name)

    def do_close_board(self, task_name: str) -> None:
        _wf._do_close_board(task_name)


class _AgentSvc:
    def __init__(self, squad_cfg) -> None:
        self._build = _wf._make_context_builder(squad_cfg)

    def build_context(
        self,
        role: str,
        agent_id: str,
        messages: list[dict],
        worktree: Path | None,
    ) -> tuple[str, str]:
        return self._build(role, agent_id, messages, worktree)

    def spawn(self, context: str, cwd: Path, model: str | None, log_path: Path | None) -> object:
        return inv.spawn(context, cwd, model, log_path)


# ---------------------------------------------------------------------------
# Run implementation
# ---------------------------------------------------------------------------


# TODO: the main run loop should be protected against unintentional ctrl+C.
#  We should require a confirmation, to avoid leaving half-merged branches and so on.
def _run(
    maxcalls: int = 1,
    dry_run: bool = False,
    squad: str = "default",
    no_tui: bool = False,
    only_role: str | None = None,
) -> None:
    _check_env_or_exit()

    squad_cfg = load_squad(squad, orc_dir=_cfg.get().orc_dir)
    logger.info(
        "orc run starting",
        maxcalls=maxcalls,
        dry_run=dry_run,
        squad=squad,
        only_role=only_role,
    )

    use_tui = not no_tui and sys.stdout.isatty()

    state: _tui.RunState | None = None
    if use_tui:
        state = _tui.RunState(
            agents=[],
            orc=_tui.OrcData(agent_id="orc", status="running", task="rebasing dev on main"),
            dev_ahead=_safe_dev_ahead(),
            telegram_ok=bool(os.environ.get("COLONY_TELEGRAM_TOKEN")),
            backend=os.environ.get("COLONY_AI_CLI", "copilot"),
            current_calls=0,
            max_calls=maxcalls,
        )

    typer.echo("⟳ Syncing dev on main…")
    messages = tg.get_messages()
    _merge_mod._rebase_dev_on_main(messages, squad_cfg)

    _board.clear_all_assignments()

    _last_dev_refresh: list[float] = [0.0]

    board_svc = _BoardSvc()
    messaging_svc = _MessagingSvc()
    workflow_svc = _WorkflowSvc(squad_cfg)
    agent_svc = _AgentSvc(squad_cfg)
    worktree_svc = _WorktreeSvc()

    hooks: _disp.DispatchHooks | None = None
    if use_tui:

        def _on_agent_start(agent: AgentProcess) -> None:
            assert state is not None
            state.agents.append(
                _tui.AgentData(
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

        def _on_orc_status(status: str, task: str | None) -> None:
            assert state is not None
            state.orc = _tui.OrcData(agent_id="orc", status=status, task=task)

        hooks = _disp.DispatchHooks(
            on_agent_start=_on_agent_start,
            on_agent_done=_on_agent_done,
            on_orc_status=_on_orc_status,
        )

    blocked_agent, blocked_state = messaging_svc.has_unresolved_block(messages)
    stalled = [(blocked_agent, blocked_state)] if blocked_agent else []
    initial_work = Work(
        open_tasks=board_svc.get_open_tasks(),
        open_visions=board_svc.get_pending_visions(),
        open_todos_and_fixmes=board_svc.scan_todos(),
        open_PRs=board_svc.get_pending_reviews(),
        stalled_agents=stalled,
    )
    if not initial_work.any_work():
        typer.echo("No pending work. Go write some vision!")
        return

    try:
        dispatcher = _disp.Dispatcher(
            squad_cfg,
            board=board_svc,
            worktree=worktree_svc,
            messaging=messaging_svc,
            workflow=workflow_svc,
            agent=agent_svc,
            hooks=hooks,
            dry_run=dry_run,
            only_role=only_role,
        )
        if use_tui and state is not None:
            # Wrap get_messages to keep state.current_loop and state.dev_ahead
            # fresh; the Textual app reads from state on its own timer.
            _orig_get_messages = messaging_svc.get_messages

            def _updating_get_messages() -> list[dict]:
                assert state is not None
                state.current_calls = dispatcher.total_agent_calls
                now = time.monotonic()
                if now - _last_dev_refresh[0] >= _DEV_AHEAD_REFRESH_INTERVAL:
                    state.dev_ahead = _safe_dev_ahead()
                    _last_dev_refresh[0] = now
                return _orig_get_messages()

            messaging_svc.get_messages = _updating_get_messages  # type: ignore[method-assign]
            _tui.run_tui(state, lambda: dispatcher.run(maxcalls=maxcalls))
        else:
            dispatcher.run(maxcalls=maxcalls)
    except Exception:
        logger.exception("orc run loop crashed")
        raise


def _safe_dev_ahead() -> int:
    """Return dev-ahead-of-main count, or 0 on error."""
    try:
        return _status_mod._dev_ahead_of_main()
    except Exception:
        logger.debug("_safe_dev_ahead: failed to compute dev-ahead count", exc_info=True)
        return 0


@app.command()
def run(
    maxcalls: Annotated[
        str,
        typer.Option(
            help=(
                "Maximum agent sessions to invoke before stopping. "
                "Pass a positive integer or 'UNLIMITED' to run until the workflow "
                "completes or hard-blocks waiting for human input. "
                "Multiple agents may be spawned in parallel within a single dispatch cycle."
            ),
        ),
    ] = "1",
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the agent context/prompt without invoking."),
    ] = False,
    squad: Annotated[
        str,
        typer.Option(
            "--squad",
            help="Squad profile name (file in .orc/squads/).  Default: 'default'.",
        ),
    ] = "default",
    no_tui: Annotated[
        bool,
        typer.Option("--no-tui", help="Disable the live TUI panel (use plain log output)."),
    ] = False,
    agent: Annotated[
        str | None,
        typer.Option(
            "--agent",
            help=(
                "Only dispatch agents with this role (coder, qa, or planner). "
                "Work for other roles is left untouched."
            ),
        ),
    ] = None,
) -> None:
    """Run the workflow, invoking agents as needed.

    Multiple agents may be spawned in parallel within a single dispatch cycle.
    Pass ``--maxcalls UNLIMITED`` to run until the workflow completes or
    hard-blocks waiting for human input."""
    if maxcalls.upper() == "UNLIMITED":
        maxcalls_int = _MAXCALLS_UNLIMITED
    else:
        try:
            maxcalls_int = int(maxcalls)
        except ValueError:
            raise typer.BadParameter(
                f"Invalid value {maxcalls!r}: must be a positive integer or 'UNLIMITED'.",
                param_hint="--maxcalls",
            )
        if maxcalls_int <= 0:
            raise typer.BadParameter(
                f"Invalid value {maxcalls!r}: must be > 0 or 'UNLIMITED'.",
                param_hint="--maxcalls",
            )

    only_role: str | None = None
    if agent is not None:
        from orc.squad import AgentRole

        normalized = agent.strip().lower()
        valid = {r.value for r in AgentRole}
        if normalized not in valid:
            raise typer.BadParameter(
                f"Invalid agent role {agent!r}. Must be one of: {', '.join(sorted(valid))}.",
                param_hint="--agent",
            )
        only_role = normalized

    return _run(
        maxcalls=maxcalls_int, dry_run=dry_run, squad=squad, no_tui=no_tui, only_role=only_role
    )
