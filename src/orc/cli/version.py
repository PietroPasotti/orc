"""orc version command."""

import typer

from orc.cli import app


def _version() -> None:
    from importlib.metadata import version as _ver

    typer.echo(_ver("qorc"))


@app.command()
def version() -> None:
    """Print the orc version."""
    return _version()
