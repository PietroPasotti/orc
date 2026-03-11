"""Tests for orc/cli/squads.py."""

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
        squads_dir.mkdir()
        (squads_dir / "test.yaml").write_text(
            "name: test\ndescription: A test squad.\n"
            "composition:\n  planner: 1\n  coder: 2\n  qa: 1\n"
            "timeout_minutes: 120\n"
        )
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path)
        result = runner.invoke(m.app, ["squads"])
        assert result.exit_code == 0
        assert "test" in result.output

    def test_squads_cli_command(self, tmp_path, monkeypatch):
        import orc.cli.squads as _squads_mod

        monkeypatch.setattr(_squads_mod, "load_all_squads", lambda agents_dir: [])
        result = runner.invoke(m.app, ["squads"])
        assert result.exit_code == 0
