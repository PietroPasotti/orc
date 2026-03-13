"""orc merge command."""

from __future__ import annotations

import structlog
import typer

import orc.cli.status as _status
import orc.config as _cfg
import orc.git.core as _git
from orc.cli import _check_env_or_exit, app
from orc.messaging import telegram as tg

logger = structlog.get_logger(__name__)


def _merge(auto: bool = False) -> None:
    _check_env_or_exit()
    messages = tg.get_messages()
    _git._rebase_dev_on_main(messages)
    dev_worktree = _git._ensure_dev_worktree()

    if auto:
        merged = _git._complete_merge()
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
