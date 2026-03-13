"""orc logs command — print or tail orc log files."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Annotated

import typer

from orc import config as _cfg
from orc.cli import app
from orc.squad import AgentRole


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
            help="Which logs to show: 'all' (default), 'orc', or a specific role or agent name. "
            "--agent coder-1 shows the logs for the agent named `coder-1` "
            "--agent coder shows the concatenated logs for all agents with role `coder`.",
        ),
    ] = "all",
    tail: Annotated[
        bool,
        typer.Option("--tail/--no-tail", help="Follow the log(s) with tail -f."),
    ] = False,
    wipe: Annotated[
        bool,
        typer.Option("--wipe", help="Delete all log files instead of printing them."),
    ] = False,
) -> None:
    """Print or tail orc log files."""
    log_dir = path if path is not None else _cfg.get().log_dir

    if wipe:
        files = sorted(log_dir.glob("*.log")) if log_dir.is_dir() else []
        if not files:
            typer.echo("No log files found.", err=True)
            raise typer.Exit(code=1)
        for f in files:
            f.unlink()
            typer.echo(f"deleted {f}")
        typer.echo(f"✓ Wiped {len(files)} log file(s) from {log_dir}")
        return

    if agent == "all":
        files = sorted(log_dir.glob("*.log")) if log_dir.is_dir() else []
    elif agent == "orc":
        files = [log_dir / "orc.log"]
    elif agent in AgentRole:
        files = sorted((log_dir / "agents").glob(f"{agent}-*.log")) if log_dir.is_dir() else []
    else:
        files = [log_dir / "agents" / f"{agent}.log"]

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
