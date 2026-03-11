"""Tests for orc/cli/version.py."""

from typer.testing import CliRunner

import orc.main as m

runner = CliRunner()


class TestVersionCommand:
    def test_version_command(self):
        result = runner.invoke(m.app, ["version"])
        assert result.exit_code == 0

    def test_version_internal(self):
        from orc.cli.version import _version

        _version()  # just check it doesn't raise
