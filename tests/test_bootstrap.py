"""Tests for orc/cli/bootstrap.py."""

import yaml
from typer.testing import CliRunner

import orc.main as m

runner = CliRunner()


# ---------------------------------------------------------------------------
# bootstrap command (integration-level tests via CLI)
# ---------------------------------------------------------------------------


class TestBootstrap:
    def test_creates_directory_structure(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(m.app, ["bootstrap"])
        assert result.exit_code == 0
        for subdir in ("roles", "squads", "vision", "work"):
            assert (tmp_path / ".orc" / subdir).is_dir()

    def test_copies_bundled_roles(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        for role in ("planner", "coder", "qa"):
            role_dir = tmp_path / ".orc" / "roles" / role
            assert role_dir.is_dir(), f"Missing {role}/ directory"
            main_file = role_dir / "_main.md"
            assert main_file.exists(), f"Missing {role}/_main.md"
            assert len(main_file.read_text()) > 100

    def test_copies_default_squad(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        squad_file = tmp_path / ".orc" / "squads" / "default.yaml"
        assert squad_file.exists()
        cfg = yaml.safe_load(squad_file.read_text())
        composition = cfg.get("composition") or cfg
        if isinstance(composition, list):
            roles = {e["role"]: e["count"] for e in composition}
            assert roles["planner"] == 1
            assert roles["coder"] == 1
        else:
            assert composition["planner"] == 1
            assert composition["coder"] == 1

    def test_creates_vision_readme(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        readme = tmp_path / ".orc" / "vision" / "README.md"
        assert readme.exists()
        assert "vision" in readme.read_text().lower()

    def test_creates_empty_board(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        board = tmp_path / ".orc" / "work" / "board.yaml"
        assert board.exists()
        data = yaml.safe_load(board.read_text())
        assert data["counter"] == 1
        assert data["open"] == []
        assert data["done"] == []

    def test_creates_justfile(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        justfile = tmp_path / ".orc" / "justfile"
        assert justfile.exists()
        content = justfile.read_text()
        assert "orc run" in content
        assert "orc status" in content
        assert "orc merge" in content

    def test_creates_env_example(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        env_example = tmp_path / ".env.example"
        assert env_example.exists()
        assert "COLONY_TELEGRAM_TOKEN" in env_example.read_text()

    def test_skips_existing_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        sentinel = "# my custom justfile"
        (tmp_path / ".orc" / "justfile").write_text(sentinel)
        runner.invoke(m.app, ["bootstrap"])
        assert (tmp_path / ".orc" / "justfile").read_text() == sentinel

    def test_force_overwrites_existing_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        (tmp_path / ".orc" / "justfile").write_text("# custom")
        runner.invoke(m.app, ["bootstrap", "--force"])
        assert "orc run" in (tmp_path / ".orc" / "justfile").read_text()

    def test_output_reports_created_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(m.app, ["bootstrap"])
        assert "Bootstrapped" in result.output
        assert "justfile" in result.output
        assert "Next steps" in result.output

    def test_output_reports_skipped_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(m.app, ["bootstrap"])
        result = runner.invoke(m.app, ["bootstrap"])
        assert "Skipped" in result.output


# ---------------------------------------------------------------------------
# _write_file helper
# ---------------------------------------------------------------------------


class TestBootstrapWriteFile:
    def test_write_file_skips_existing(self, tmp_path):
        from orc.cli.bootstrap import _write_file

        existing = tmp_path / "test.txt"
        existing.write_text("original")
        created, skipped = [], []
        _write_file(existing, "new content", created, skipped)
        assert existing.read_text() == "original"
        assert str(existing) in skipped
        assert not created

    def test_write_file_creates_new(self, tmp_path):
        from orc.cli.bootstrap import _write_file

        new_file = tmp_path / "new.txt"
        created, skipped = [], []
        _write_file(new_file, "hello", created, skipped)
        assert new_file.read_text() == "hello"
        assert str(new_file) in created
        assert not skipped
