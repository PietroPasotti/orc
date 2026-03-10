"""orc squads command."""

from __future__ import annotations

import structlog
import typer

import orc.config as _cfg
from orc import logger as _obs
from orc.cli import app
from orc.squad import load_all_squads

logger = structlog.get_logger(__name__)


def _squads() -> None:
    _obs.setup()
    profiles = load_all_squads(agents_dir=_cfg.AGENTS_DIR)
    if not profiles:
        typer.echo("No squad profiles found.")
        return

    typer.echo("\nAvailable squad profiles:\n")
    for cfg in profiles:
        coder_label = f"{cfg.coder} coder{'s' if cfg.coder != 1 else ''}"
        qa_label = f"{cfg.qa} QA"
        composition = f"1 planner · {coder_label} · {qa_label} · {cfg.timeout_minutes} min"
        typer.echo(f"  {cfg.name:<12}  {composition}")
        if cfg.description:
            for line in cfg.description.strip().splitlines():
                typer.echo(f"               {line}")
        typer.echo("")


@app.command()
def squads() -> None:
    """List available squad profiles and their composition."""
    return _squads()
