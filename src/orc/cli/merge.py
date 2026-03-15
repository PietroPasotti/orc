"""orc merge command."""

from __future__ import annotations

import structlog
import typer

import orc.cli.status as _status
import orc.config as _cfg
import orc.engine.workflow as _wf
from orc.cli import _check_env_or_exit, app
from orc.git import Git, UntrackedMergeBlockError

logger = structlog.get_logger(__name__)


def _merge(auto: bool = False) -> None:
    _check_env_or_exit()
    _wf.rebase_dev_on_main()

    if auto:
        cfg = _cfg.get()
        try:
            merged = Git(cfg.repo_root).merge_ff_only(cfg.work_dev_branch)
        except UntrackedMergeBlockError as exc:
            for f in exc.files:
                typer.echo(f"✗ {f} exists as untracked in main worktree; remove it and re-run")
            raise typer.Exit(code=1)
        if merged:
            typer.echo("✓ dev merged into main.")
        else:
            typer.echo("Already up to date.")
    else:
        if _status._dev_ahead_of_main() == 0:
            typer.echo("Nothing to merge — dev has no commits ahead of main.")
            return
        cfg = _cfg.get()
        typer.echo(
            f"✓ dev is up-to-date with main and ready to merge.\n"
            f"  Run the following to merge manually:\n\n"
            f"    git -C {cfg.repo_root} merge --ff-only {cfg.work_dev_branch}\n\n"
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
