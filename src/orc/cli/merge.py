"""orc merge command."""

from __future__ import annotations

import subprocess

import structlog
import typer

import orc.config as _cfg
import orc.context as _ctx
import orc.git as _git
from orc import telegram as tg
from orc.cli import _check_env_or_exit, app
from orc.squad import SquadConfig

logger = structlog.get_logger(__name__)


def _rebase_dev_on_main(messages: list, squad_cfg: SquadConfig | None = None) -> None:
    """Rebase dev on top of main so every session starts with the latest instructions."""
    dev_worktree = _git._ensure_dev_worktree()

    result = subprocess.run(
        ["git", "rebase", "main"], cwd=dev_worktree, capture_output=True, text=True
    )
    if result.returncode == 0:
        typer.echo("✓ dev rebased on main.")
        return

    if "unstaged changes" in result.stderr or "uncommitted changes" in result.stderr:
        status_output = _git._conflict_status(dev_worktree)
        typer.echo(
            f"✗ Cannot rebase: the dev worktree has unstaged changes.\n\n"
            f"{status_output}\n\n"
            f"Please commit or stash these changes in the dev worktree, then retry `orc merge`."
        )
        raise typer.Exit(code=1)

    status_output = _git._conflict_status(dev_worktree)
    typer.echo(f"⚠ Startup rebase conflict:\n{status_output}\nDelegating to coder agent…")

    conflict_extra = (
        "## Startup rebase conflict — your task\n\n"
        f"A `git rebase main` of the `{_cfg.WORK_DEV_BRANCH}` branch was attempted at session "
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

    coder_model = squad_cfg.model("coder") if squad_cfg is not None else _ctx._DEFAULT_MODEL
    model, context = _ctx.build_agent_context(
        "coder", messages, extra=conflict_extra, model=coder_model
    )
    rc = _ctx.invoke_agent("coder", context, model)

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


def _merge() -> None:
    _check_env_or_exit()
    messages = tg.get_messages()
    _rebase_dev_on_main(messages)
    dev_worktree = _git._ensure_dev_worktree()
    _git._complete_merge(dev_worktree)
    typer.echo("✓ dev merged into main.")


@app.command()
def merge() -> None:
    """Rebase dev on top of main and fast-forward merge dev into main.

    If the rebase produces conflicts the coder agent is invoked to resolve them.
    Once the agent exits the merge is completed automatically.
    """
    return _merge()
