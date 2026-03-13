"""orc merge command."""

from __future__ import annotations

import subprocess

import structlog
import typer

import orc.cli.status as _status
import orc.config as _cfg
import orc.engine.context as _ctx
import orc.git.core as _git
from orc.cli import _check_env_or_exit, app
from orc.messaging import telegram as tg
from orc.squad import AgentRole, SquadConfig

logger = structlog.get_logger(__name__)


def _rebase_dev_on_main(messages: list, squad_cfg: SquadConfig | None = None) -> None:
    """Rebase dev on top of main so every session starts with the latest instructions."""
    dev_worktree = _git._ensure_dev_worktree()

    result = subprocess.run(
        ["git", "rebase", "--autostash", "main"], cwd=dev_worktree, capture_output=True, text=True
    )
    if result.returncode == 0:
        typer.echo("✓ dev rebased on main.")
        return

    status_output = _git._conflict_status(dev_worktree)
    typer.echo(f"⚠ Startup rebase conflict:\n{status_output}\nDelegating to coder agent…")

    conflict_extra = (
        "## Startup rebase conflict — your task\n\n"
        f"A `git rebase main` of the `{_cfg.get().work_dev_branch}` "
        "branch was attempted at session "
        "start and stopped with conflicts.  The rebase is currently paused in the dev "
        "worktree.\n\n"
        f"Conflicting files (from `git status --short`):\n```\n{status_output}\n```\n\n"
        "**What you must do:**\n"
        "1. Open each conflicting file, resolve the conflict markers (`<<<<<<<`, "
        "`=======`, `>>>>>>>`).\n"
        "2. `git add <resolved-file>` for each resolved file.\n"
        "3. `git rebase --continue` (repeat steps 1–3 if git stops again).\n"
        "4. Do NOT `git rebase --abort`. Finish the rebase.\n"
        "5. Exit when the rebase is complete.\n"
    )

    coder_model = squad_cfg.model(AgentRole.CODER) if squad_cfg is not None else _ctx._DEFAULT_MODEL
    model, context = _ctx.build_agent_context(
        AgentRole.CODER, messages, extra=conflict_extra, model=coder_model
    )
    rc = _ctx.invoke_agent(AgentRole.CODER, context, model)

    if rc != 0:
        logger.error("coder agent failed to resolve startup rebase", exit_code=rc)
        typer.echo(f"✗ Coder agent exited with code {rc} while resolving startup rebase.")
        raise typer.Exit(code=rc)

    if _git._rebase_in_progress(dev_worktree):
        logger.error("rebase still in progress after coder exited")
        typer.echo("✗ Rebase still in progress after agent exit. Manual intervention needed.")
        raise typer.Exit(code=1)

    logger.info("dev rebased on main after conflict resolution by coder")
    typer.echo("✓ dev rebased on main (conflicts resolved by coder).")


def _merge(auto: bool = False) -> None:
    _check_env_or_exit()
    messages = tg.get_messages()
    _rebase_dev_on_main(messages)
    dev_worktree = _git._ensure_dev_worktree()

    if auto:
        merged = _git._complete_merge(dev_worktree)
        if merged:
            typer.echo("✓ dev merged into main.")
        else:
            typer.echo("Already up to date.")
    else:
        if _status._dev_ahead_of_main() == 0:
            typer.echo("Nothing to merge — dev has no commits ahead of main.")
            return
        typer.echo(
            f"✓ dev is up-to-date with main and ready to merge.\n"
            f"  Run the following to merge manually:\n\n"
            f"    git -C {dev_worktree} checkout main\n"
            f"    git -C {dev_worktree} merge --ff-only {_cfg.get().work_dev_branch}\n\n"
            f"  Or re-run with --auto to let orc do it."
        )


@app.command()
def merge(
    auto: bool = typer.Option(
        False, "--auto", help="Actually merge dev into main (default: verify-only)."
    ),
) -> None:
    """Rebase dev on top of main and verify it is ready to merge.

    By default only the rebase is performed and the user is prompted to merge
    manually.  Pass ``--auto`` to also fast-forward merge dev into main.

    If the rebase produces conflicts the coder agent is invoked to resolve them.
    """
    return _merge(auto=auto)
