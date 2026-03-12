"""orc — CLI entry point.

The ``orc`` console script is declared as ``orc.main:app`` in pyproject.toml.
All application logic lives in the subpackages; this module exists solely to
satisfy that entry-point contract.
"""

from orc.cli import app  # noqa: F401

if __name__ == "__main__":  # pragma: no cover
    app()
