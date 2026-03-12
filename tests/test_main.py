"""Tests for orc/main.py (CLI entry point)."""

import typer

import orc.main as m


def test_main_exports_app():
    """main.py re-exports the Typer app as the CLI entry point."""
    assert isinstance(m.app, typer.Typer)
