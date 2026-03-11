"""CLI package — defines the typer app and registers all commands."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import structlog
import typer

import orc.config as _cfg
from orc import logger as _obs

logger = structlog.get_logger(__name__)

app = typer.Typer(name="orc", help="orc multi-agent orchestrator.", no_args_is_help=True)


@app.callback()
def _app_entry(
    config_dir: Annotated[
        Path | None,
        typer.Option(
            "--config-dir",
            help=(
                "Base directory to search for the orc configuration folder. "
                "orc looks for <config-dir>/.orc/ then <config-dir>/orc/. "
                "Defaults to the current working directory."
            ),
            show_default=False,
        ),
    ] = None,
    project_dir: Annotated[
        Path | None,
        typer.Option(
            "--project-dir",
            help=(
                "Project root directory. orc will change to this directory before "
                "resolving paths, loading .env, and running git commands. "
                "Defaults to the current working directory."
            ),
            show_default=False,
        ),
    ] = None,
) -> None:
    """Bootstrap observability and resolve the config directory."""
    _obs.setup()
    if project_dir is not None:
        os.chdir(project_dir.resolve())
    if config_dir is not None:
        found = _cfg._find_config_dir(base=config_dir)
        if found is None:
            typer.echo(
                f"✗ No orc config directory found in '{config_dir}'.\n"
                f"  Expected '{config_dir}/.orc/' or '{config_dir}/orc/'.\n"
                "  Run 'orc bootstrap' to create one.",
                err=True,
            )
            raise typer.Exit(code=1)
        _cfg._init_paths(found, repo_root=Path.cwd())
    elif project_dir is not None:
        found = _cfg._find_config_dir()
        if found is not None:
            _cfg._init_paths(found)


def _check_env_or_exit() -> None:
    if not _cfg.AGENTS_DIR.is_dir():
        typer.echo(
            f"✗ orc configuration directory not found.\n"
            f"  Searched: {_cfg.AGENTS_DIR.parent}/.orc/  and  {_cfg.AGENTS_DIR.parent}/orc/\n"
            "  Run 'orc bootstrap' to create one, or pass --config-dir <base> to "
            "point to an existing configuration.",
            err=True,
        )
        raise typer.Exit(code=1)
    errors = _cfg.validate_env()
    if errors:
        typer.echo("✗ Configuration errors — fix .env before running:\n", err=True)
        for err in errors:
            typer.echo(f"  • {err}", err=True)
        raise typer.Exit(code=1)


# Import command modules LAST to avoid circular imports
from orc.cli import bootstrap, logs, merge, run, squads, status, version  # noqa: E402, F401
