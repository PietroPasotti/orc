"""orc logs command — print or tail orc log files."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Annotated

import typer

from orc import config as _cfg
from orc.cli import app

_DEFAULT_LOG_DIR = _cfg.LOG_DIR


@app.command("logs")
def _logs(
    path: Annotated[
        Path | None,
        typer.Option("--path", help="Log directory (default: .orc/logs)."),
    ] = None,
    agent: Annotated[
        str,
        typer.Option(
            "--agent",
            help="Which logs to show: 'all' (default), 'orc', or a specific agent name.",
        ),
    ] = "all",
    tail: Annotated[
        bool,
        typer.Option("--tail/--no-tail", help="Follow the log(s) with tail -f."),
    ] = False,
) -> None:
    """Print or tail orc log files."""
    log_dir = path if path is not None else _DEFAULT_LOG_DIR

    if agent == "all":
        files = sorted(log_dir.glob("*.log")) if log_dir.is_dir() else []
    elif agent == "orc":
        files = [log_dir / "orc.log"]
    else:
        files = [log_dir / f"{agent}.log"]

    existing = [f for f in files if f.exists()]
    missing = [f for f in files if not f.exists()]

    for f in missing:
        typer.echo(f"warning: log file not found: {f}", err=True)

    if not existing:
        typer.echo("No log files found.", err=True)
        raise typer.Exit(code=1)

    if tail:
        cmd = ["tail", "-f", *[str(f) for f in existing]]
    else:
        cmd = ["cat", *[str(f) for f in existing]]

    subprocess.run(cmd)  # noqa: S603
