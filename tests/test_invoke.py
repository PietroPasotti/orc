"""Tests for orc/invoke.py – credential resolution and CLI dispatch."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conftest import FakePopen

# Import after conftest has stubbed out dotenv
from orc.ai import backends as bk
from orc.ai import invoke as iv
from orc.ai.backends import SpawnResult

# ---------------------------------------------------------------------------
# _require_config
# ---------------------------------------------------------------------------


class TestRequireConfig:
    def test_valid_copilot(self, monkeypatch):
        monkeypatch.setattr(iv, "_CLI", "copilot")
        iv._require_config()  # must not raise

    def test_valid_claude(self, monkeypatch):
        monkeypatch.setattr(iv, "_CLI", "claude")
        iv._require_config()  # must not raise

    def test_invalid_raises(self, monkeypatch):
        monkeypatch.setattr(iv, "_CLI", "gpt")
        with pytest.raises(EnvironmentError, match="not supported"):
            iv._require_config()


# ---------------------------------------------------------------------------
# invoke()
# ---------------------------------------------------------------------------


class TestInvoke:
    def test_copilot_dispatch(self, monkeypatch):
        monkeypatch.setattr(iv, "_CLI", "copilot")
        monkeypatch.setenv("GH_TOKEN", "ghp_test")
        mock_run = MagicMock(return_value=MagicMock(returncode=0))
        with patch("subprocess.run", mock_run):
            code = iv.invoke("do the thing")
        assert code == 0
        cmd = mock_run.call_args[0][0]
        assert cmd[:3] == ["copilot", "--yolo", "--prompt"]
        # Context is written to a temp file and passed as @filepath
        assert cmd[3].startswith("@")
        assert mock_run.call_args[1]["env"]["GH_TOKEN"] == "ghp_test"

    def test_claude_dispatch(self, monkeypatch):
        monkeypatch.setattr(iv, "_CLI", "claude")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        mock_run = MagicMock(return_value=MagicMock(returncode=0))
        with patch("subprocess.run", mock_run):
            code = iv.invoke("do the thing")
        assert code == 0
        cmd = mock_run.call_args[0][0]
        assert cmd[:2] == ["claude", "-p"]
        assert mock_run.call_args[1]["env"]["ANTHROPIC_API_KEY"] == "sk-ant-test"

    def test_claude_dispatch_with_model(self, monkeypatch):
        monkeypatch.setattr(iv, "_CLI", "claude")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        mock_run = MagicMock(return_value=MagicMock(returncode=0))
        with patch("subprocess.run", mock_run):
            iv.invoke("do the thing", model="claude-3-5-sonnet")
        cmd = mock_run.call_args[0][0]
        assert "--model" in cmd
        assert "claude-3-5-sonnet" in cmd

    def test_copilot_forwards_cwd(self, monkeypatch, tmp_path):
        monkeypatch.setattr(iv, "_CLI", "copilot")
        monkeypatch.setenv("GH_TOKEN", "ghp_test")
        mock_run = MagicMock(return_value=MagicMock(returncode=0))
        with patch("subprocess.run", mock_run):
            iv.invoke("do the thing", cwd=tmp_path)
        assert mock_run.call_args[1]["cwd"] == tmp_path

    def test_invalid_cli_raises(self, monkeypatch):
        monkeypatch.setattr(iv, "_CLI", "unknown")
        with pytest.raises(EnvironmentError, match="not supported"):
            iv.invoke("do the thing")

    def test_copilot_missing_token_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(iv, "_CLI", "copilot")
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.setattr(bk.CopilotBackend, "APPS_JSON", tmp_path / "nonexistent.json")
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(EnvironmentError, match="GH_TOKEN"):
                iv.invoke("do the thing")

    def test_claude_missing_key_raises(self, monkeypatch):
        monkeypatch.setattr(iv, "_CLI", "claude")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
            iv.invoke("do the thing")


# ---------------------------------------------------------------------------
# spawn() coverage tests (from test_coverage.py)
# ---------------------------------------------------------------------------


class TestInvokeSpawn:
    def test_spawn_copilot(self, tmp_path, monkeypatch):
        from pathlib import Path
        from unittest.mock import patch

        monkeypatch.setattr(iv, "_CLI", "copilot")
        monkeypatch.setattr(bk.CopilotBackend, "resolve_token", lambda self: "ghp_test")

        fake_proc = FakePopen()
        with patch("orc.ai.backends.subprocess.Popen", return_value=fake_proc):
            result = iv.spawn("ctx text", cwd=tmp_path, model="gpt-4", log_path=None)
        assert isinstance(result, SpawnResult)
        assert result.process is fake_proc
        assert result.log_fh is None
        Path(result.context_tmp).unlink(missing_ok=True)

    def test_spawn_claude(self, tmp_path, monkeypatch):
        from unittest.mock import patch

        monkeypatch.setattr(iv, "_CLI", "claude")
        monkeypatch.setattr(bk.ClaudeBackend, "resolve_key", lambda self: "key123")

        fake_proc = FakePopen()
        with patch("orc.ai.backends.subprocess.Popen", return_value=fake_proc):
            result = iv.spawn("ctx text", cwd=tmp_path, model="claude-3", log_path=None)
        assert isinstance(result, SpawnResult)
        assert result.process is fake_proc
        Path(result.context_tmp).unlink(missing_ok=True)

    def test_spawn_with_log_path(self, tmp_path, monkeypatch):
        from unittest.mock import patch

        monkeypatch.setattr(iv, "_CLI", "copilot")
        monkeypatch.setattr(bk.CopilotBackend, "resolve_token", lambda self: "ghp_test")

        log_path = tmp_path / "agent.log"
        fake_proc = FakePopen()
        with patch("orc.ai.backends.subprocess.Popen", return_value=fake_proc):
            result = iv.spawn("ctx", cwd=tmp_path, model="m", log_path=log_path)
        assert isinstance(result, SpawnResult)
        assert result.log_fh is not None
        result.log_fh.close()
        Path(result.context_tmp).unlink(missing_ok=True)
