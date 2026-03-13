"""Tests for orc/cli/squads.py."""

from dataclasses import replace as _replace

from typer.testing import CliRunner

import orc.config as _cfg
import orc.main as m

runner = CliRunner()


class TestSquadsCommand:
    def test_squads_no_profiles(self, tmp_path, monkeypatch):
        import orc.cli.squads as _squads_mod

        monkeypatch.setattr(_squads_mod, "load_all_squads", lambda agents_dir: [])
        result = runner.invoke(m.app, ["squads"])
        assert result.exit_code == 0
        assert "No squad profiles found" in result.output

    def test_squads_with_profiles(self, tmp_path, monkeypatch):
        squads_dir = tmp_path / "squads"
        squads_dir.mkdir(exist_ok=True)
        (squads_dir / "test.yaml").write_text(
            "name: test\ndescription: A test squad.\n"
            "composition:\n"
            "  - role: planner\n    count: 1\n"
            "  - role: coder\n    count: 2\n"
            "  - role: qa\n    count: 1\n"
            "timeout_minutes: 120\n"
        )
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), agents_dir=tmp_path))
        result = runner.invoke(m.app, ["squads"])
        assert result.exit_code == 0
        assert "test" in result.output

    def test_squads_cli_command(self, tmp_path, monkeypatch):
        import orc.cli.squads as _squads_mod

        monkeypatch.setattr(_squads_mod, "load_all_squads", lambda agents_dir: [])
        result = runner.invoke(m.app, ["squads"])
        assert result.exit_code == 0
