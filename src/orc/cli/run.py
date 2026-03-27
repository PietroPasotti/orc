"""orc run command."""

from __future__ import annotations

import atexit
import os
import sys
import time
from typing import Annotated

import structlog
import typer

import orc.cli.status as _status_mod
import orc.config as _cfg
import orc.engine.context as _ctx
import orc.engine.workflow as _wf
from orc.cli import _check_env_or_exit, app
from orc.cli import tui as _tui
from orc.coordination import BoardStateManager, CoordinationServer
from orc.engine import dispatcher as _disp
from orc.engine.context import TodoItem
from orc.engine.pool import AgentProcess
from orc.engine.services import BoardService
from orc.messaging import telegram as tg
from orc.squad import AgentRole, load_squad

_MAXCALLS_UNLIMITED = sys.maxsize

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Service adapters — satisfy the Protocol contracts using module functions
# ---------------------------------------------------------------------------


class _BoardSvc(BoardStateManager, BoardService):
    """BoardStateManager extended with git/filesystem queries for the run command."""

    def get_pending_reviews(self) -> list[str]:
        return _status_mod._pending_reviews()

    def scan_todos(self) -> list[TodoItem]:
        return _ctx._scan_todos(_cfg.get().dev_worktree)

    def is_empty(self) -> bool:
        return not (
            self.get_tasks()
            or self.get_pending_visions()
            or self.scan_todos()
            or self.get_pending_reviews()
            or self.get_blocked_tasks()
        )


# ---------------------------------------------------------------------------
# Run implementation
# ---------------------------------------------------------------------------


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

    state = _tui.RunState(
        agents=[],
        orc=_tui.OrcData(agent_id="orc", status="running", task="booting") if use_tui else None,
        features_done=_safe_features_done(),
        stuck_tasks=0,
        telegram_ok=bool(os.environ.get("COLONY_TELEGRAM_TOKEN")),
        backend="internal",
        current_calls=0,
        max_calls=maxcalls,
        squad_name=squad_cfg.name,
        squad_repr=(
            f"{squad_cfg.name}"
            f" ({squad_cfg.planner}-{squad_cfg.coder}-{squad_cfg.merger}-{squad_cfg.qa})"
        ),
        run_started_at=time.monotonic(),
    )

    typer.echo("⟳ Syncing dev on main…")
    _wf.rebase_dev_on_main(squad_cfg)

    # Start the coordination API server so agent tools in worktrees always
    # write to the correct (main) .orc/ directory.
    cfg = _cfg.get()
    _coord_state = _BoardSvc(cfg.orc_dir)
    _coord_state.clear_all_assignments()
    _coord_server = CoordinationServer(_coord_state, cfg.api_socket_path)
    _coord_server.start()
    os.environ["ORC_API_SOCKET"] = str(cfg.api_socket_path)
    atexit.register(_coord_server.stop)

    messaging_svc = tg.TelegramMessagingService()
    workflow_svc = _wf.WorkflowSvc(squad_cfg)
    agent_svc = _wf.AgentSvc(squad_cfg, board=_coord_state)
    worktree_svc = _wf.WorktreeManager()

    def _on_agent_start(agent: AgentProcess) -> None:
        state.current_calls += 1
        state.agent_calls[agent.role] += 1

    hooks: _disp.DispatchHooks | None = None
    if use_tui:

        def _on_agent_start_tui(agent: AgentProcess) -> None:
            _on_agent_start(agent)
            details: str | None = None
            if agent.role == AgentRole.PLANNER:
                todos = _coord_state.scan_todos()
                visions = _coord_state.get_pending_visions()
                vision_names = [v.removesuffix(".md") for v in visions]

                # todo: make this dict[str,str] and display as key: value pairs in the TUI
                parts: list[str] = []
                if todos:
                    parts.append(f"{len(todos)} todo(s)")
                if vision_names:
                    parts.append("visions: " + ", ".join(vision_names))
                details = "  ".join(parts) if parts else None
            state.agents.append(
                _tui.AgentData(
                    agent_id=agent.agent_id,
                    role=agent.role,
                    model=agent.model,
                    status="running",
                    task_name=agent.task_name,
                    worktree=str(agent.worktree),
                    started_at=agent.started_at,
                    details=details,
                )
            )

        def _on_agent_done(agent: AgentProcess, rc: int) -> None:
            state.agents = [r for r in state.agents if r.agent_id != agent.agent_id]

        def _on_orc_status(task: str) -> None:
            state.orc = _tui.OrcData(agent_id="orc", status="running", task=task)

        def _on_feature_merged() -> None:
            state.features_done = _safe_features_done()

        def _on_cycle() -> None:
            state.features_done = _safe_features_done()
            state.stuck_tasks = sum(1 for t in _coord_state.get_tasks() if t.status == "stuck")
            state.dispatcher_phase = dispatcher.phase

        hooks = _disp.DispatchHooks(
            on_agent_start=_on_agent_start_tui,
            on_agent_done=_on_agent_done,
            on_orc_status=_on_orc_status,
            on_feature_merged=_on_feature_merged,
            on_cycle=_on_cycle,
        )
    else:
        hooks = _disp.DispatchHooks(on_agent_start=_on_agent_start)

    if _coord_state.is_empty():
        typer.echo("No pending work. Go write some vision!")
        return

    try:
        dispatcher = _disp.Dispatcher(
            squad_cfg,
            board=_coord_state,
            worktree=worktree_svc,
            messaging=messaging_svc,
            workflow=workflow_svc,
            agent=agent_svc,
            hooks=hooks,
            dry_run=dry_run,
            only_role=only_role,
        )
        if use_tui:

            def _drain() -> None:
                dispatcher.phase = _disp.DispatcherPhase.DRAINING
                state.dispatcher_phase = dispatcher.phase

            def _abort() -> None:
                dispatcher._kill_all_and_unassign()

            _tui.run_tui(
                state,
                lambda: dispatcher.run(maxcalls=maxcalls),
                on_drain=_drain,
                on_abort=_abort,
            )
        else:
            dispatcher.run(maxcalls=maxcalls)
    except KeyboardInterrupt:
        _coord_server.stop()
        typer.echo(
            "\n⚠ Interrupted. The dev branch and board may be in a partial "
            "state. Run `orc run` again to resume.",
            err=True,
        )
        raise typer.Exit(code=1)
    except Exception:
        _coord_server.stop()
        logger.exception("orc run loop crashed")
        raise


def _safe_features_done() -> int:
    """Return the count of feature-merge commits in dev not yet in main, or 0 on error."""
    try:
        return len(_wf.features_in_dev_not_main())
    except Exception:
        logger.debug("_safe_features_done: failed to count features", exc_info=True)
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
