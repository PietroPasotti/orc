"""Tests for orc/main.py (re-export shim)."""

import orc.main as m


def test_main_exports_app():
    """main.py re-exports the Typer app."""
    import typer

    assert isinstance(m.app, typer.Typer)


def test_main_re_exports_core_symbols():
    """Spot-check that key re-exports are present."""
    assert callable(m.validate_env)
    assert callable(m.determine_next_agent)
    assert callable(m.wait_for_human_reply)
    assert callable(m._boot_message_body)
