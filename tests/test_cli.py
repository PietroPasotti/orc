"""Tests for orc/cli/__init__.py."""

from dataclasses import replace as _replace
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

import orc.config as _cfg
import orc.main as m

runner = CliRunner()


class TestCliInitCoverage:
    def test_check_env_or_exit_exits_when_agents_dir_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            _cfg, "_config", _replace(_cfg.get(), agents_dir=tmp_path / "nonexistent")
        )
        result = runner.invoke(m.app, ["run"])
        assert result.exit_code != 0
        assert "orc configuration directory not found" in (result.output or "")

    def test_check_env_or_exit_exits_on_validation_errors(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_cfg, "validate_env", lambda: ["COLONY_TELEGRAM_TOKEN is not set."])
        result = runner.invoke(m.app, ["run"])
        assert result.exit_code != 0
        assert "Configuration errors" in (result.output or "")

    def test_app_entry_with_config_dir_not_found(self, tmp_path, monkeypatch, _init_config):
        empty = tmp_path / "empty"
        empty.mkdir(exist_ok=True)
        monkeypatch.setattr(_cfg, "init", _init_config)
        result = runner.invoke(m.app, ["--config-dir", str(empty), "version"])
        assert result.exit_code != 0
        assert "No orc config directory found" in (result.output or "")

    def test_app_entry_with_valid_config_dir(self, tmp_path, monkeypatch, _init_config):
        orc_dir = tmp_path / ".orc"
        orc_dir.mkdir(exist_ok=True)
        monkeypatch.setattr(_cfg, "init", _init_config)
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])
        result = runner.invoke(m.app, ["--config-dir", str(tmp_path), "version"])
        assert result.exit_code == 0

    def test_app_entry_project_dir_changes_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(Path.cwd())
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])
        result = runner.invoke(m.app, ["--project-dir", str(tmp_path), "version"])
        assert result.exit_code == 0

    def test_app_entry_project_dir_with_auto_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(Path.cwd())
        orc_dir = tmp_path / ".orc"
        orc_dir.mkdir(exist_ok=True)
        monkeypatch.setattr(_cfg, "validate_env", lambda: [])
        result = runner.invoke(m.app, ["--project-dir", str(tmp_path), "version"])
        assert result.exit_code == 0


class TestLogsCommand:
    def test_logs_no_files_exits_nonzero(self, tmp_path):
        result = runner.invoke(m.app, ["logs", "--path", str(tmp_path)])
        assert result.exit_code != 0
        assert "No log files found" in result.output

    def test_logs_agent_all_prints_existing_files(self, tmp_path):
        (tmp_path / "orc.log").write_text("orchestrator log\n")
        (tmp_path / "coder-1.log").write_text("coder log\n")
        with patch("subprocess.run") as mock_run:
            result = runner.invoke(m.app, ["logs", "--path", str(tmp_path)])
        assert result.exit_code == 0
        called_cmd = mock_run.call_args[0][0]
        assert called_cmd[0] == "cat"
        assert any("orc.log" in c for c in called_cmd)
        assert any("coder-1.log" in c for c in called_cmd)

    def test_logs_agent_orc_only(self, tmp_path):
        (tmp_path / "orc.log").write_text("orc log\n")
        with patch("subprocess.run") as mock_run:
            result = runner.invoke(m.app, ["logs", "--path", str(tmp_path), "--agent", "orc"])
        assert result.exit_code == 0
        called_cmd = mock_run.call_args[0][0]
        assert any("orc.log" in c for c in called_cmd)

    def test_logs_agent_named(self, tmp_path):
        (tmp_path / "coder-1.log").write_text("coder log\n")
        with patch("subprocess.run") as mock_run:
            result = runner.invoke(m.app, ["logs", "--path", str(tmp_path), "--agent", "coder-1"])
        assert result.exit_code == 0
        called_cmd = mock_run.call_args[0][0]
        assert any("coder-1.log" in c for c in called_cmd)

    def test_logs_tail_flag(self, tmp_path):
        (tmp_path / "orc.log").write_text("log\n")
        with patch("subprocess.run") as mock_run:
            result = runner.invoke(
                m.app, ["logs", "--path", str(tmp_path), "--agent", "orc", "--tail"]
            )
        assert result.exit_code == 0
        called_cmd = mock_run.call_args[0][0]
        assert called_cmd[0] == "tail"
        assert "-f" in called_cmd

    def test_logs_agent_role_globs_matching_files(self, tmp_path):
        (tmp_path / "coder-1.log").write_text("coder-1 log\n")
        (tmp_path / "coder-2.log").write_text("coder-2 log\n")
        with patch("subprocess.run") as mock_run:
            result = runner.invoke(m.app, ["logs", "--path", str(tmp_path), "--agent", "coder"])
        assert result.exit_code == 0
        called_cmd = mock_run.call_args[0][0]
        assert any("coder-1.log" in c for c in called_cmd)
        assert any("coder-2.log" in c for c in called_cmd)

    def test_logs_missing_agent_warns(self, tmp_path):
        result = runner.invoke(m.app, ["logs", "--path", str(tmp_path), "--agent", "nonexistent"])
        assert result.exit_code != 0
        assert "warning: log file not found" in result.output

    def test_logs_all_nonexistent_dir(self, tmp_path):
        nonexistent = tmp_path / "missing"
        result = runner.invoke(m.app, ["logs", "--path", str(nonexistent)])
        assert result.exit_code != 0
        assert "No log files found" in result.output

    def test_logs_wipe_deletes_files(self, tmp_path):
        (tmp_path / "orc.log").write_text("orc log\n")
        (tmp_path / "coder-1.log").write_text("coder log\n")
        result = runner.invoke(m.app, ["logs", "--path", str(tmp_path), "--wipe"])
        assert result.exit_code == 0
        assert "Wiped 2 log file(s)" in result.output
        assert not any(tmp_path.glob("*.log"))

    def test_logs_wipe_no_files_exits_nonzero(self, tmp_path):
        result = runner.invoke(m.app, ["logs", "--path", str(tmp_path), "--wipe"])
        assert result.exit_code != 0
        assert "No log files found" in result.output
