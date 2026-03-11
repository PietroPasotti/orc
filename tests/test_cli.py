"""Tests for orc/cli/__init__.py."""

from pathlib import Path

from typer.testing import CliRunner

import orc.config as _cfg
import orc.main as m

runner = CliRunner()


class TestCliInitCoverage:
    def test_check_env_or_exit_exits_when_agents_dir_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path / "nonexistent")
        result = runner.invoke(m.app, ["run"])
        assert result.exit_code != 0
        assert "orc configuration directory not found" in (result.output or "")

    def test_check_env_or_exit_exits_on_validation_errors(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path)
        monkeypatch.setattr(_cfg, "validate_env", lambda: ["COLONY_TELEGRAM_TOKEN is not set."])
        result = runner.invoke(m.app, ["run"])
        assert result.exit_code != 0
        assert "Configuration errors" in (result.output or "")

    def test_app_entry_with_config_dir_not_found(self, tmp_path):
        result = runner.invoke(m.app, ["--config-dir", str(tmp_path), "version"])
        assert result.exit_code != 0
        assert "No orc config directory found" in (result.output or "")

    def test_app_entry_with_valid_config_dir(self, tmp_path, monkeypatch):
        orc_dir = tmp_path / ".orc"
        orc_dir.mkdir()
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])
        result = runner.invoke(m.app, ["--config-dir", str(tmp_path), "version"])
        assert result.exit_code == 0

    def test_app_entry_project_dir_changes_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(Path.cwd())
        for attr in (
            "AGENTS_DIR",
            "REPO_ROOT",
            "DEV_WORKTREE",
            "WORK_DIR",
            "BOARD_FILE",
            "ROLES_DIR",
            "ENV_FILE",
            "WORKTREE_BASE",
        ):
            monkeypatch.setattr(_cfg, attr, getattr(_cfg, attr))
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])
        result = runner.invoke(m.app, ["--project-dir", str(tmp_path), "version"])
        assert result.exit_code == 0

    def test_app_entry_project_dir_with_auto_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(Path.cwd())
        for attr in (
            "AGENTS_DIR",
            "REPO_ROOT",
            "DEV_WORKTREE",
            "WORK_DIR",
            "BOARD_FILE",
            "ROLES_DIR",
            "ENV_FILE",
            "WORKTREE_BASE",
        ):
            monkeypatch.setattr(_cfg, attr, getattr(_cfg, attr))
        orc_dir = tmp_path / ".orc"
        orc_dir.mkdir()
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])
        result = runner.invoke(m.app, ["--project-dir", str(tmp_path), "version"])
        assert result.exit_code == 0
