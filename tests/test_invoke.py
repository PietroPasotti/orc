"""Tests for orc/invoke.py – credential resolution and CLI dispatch."""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

# Import after conftest has stubbed out dotenv
from orc import invoke as iv


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
# _resolve_gh_token
# ---------------------------------------------------------------------------


class TestResolveGhToken:
    def test_env_var_takes_priority(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GH_TOKEN", "ghp_env_token")
        monkeypatch.setattr(iv, "_COPILOT_APPS_JSON", tmp_path / "nonexistent.json")
        assert iv._resolve_gh_token() == "ghp_env_token"

    def test_apps_json_fallback(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GH_TOKEN", raising=False)
        apps = tmp_path / "apps.json"
        apps.write_text(json.dumps({"entry": {"oauth_token": "ghp_apps_json"}}))
        monkeypatch.setattr(iv, "_COPILOT_APPS_JSON", apps)
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert iv._resolve_gh_token() == "ghp_apps_json"

    def test_gh_cli_fallback(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.setattr(iv, "_COPILOT_APPS_JSON", tmp_path / "nonexistent.json")
        with patch("subprocess.run", return_value=MagicMock(stdout="ghp_gh_cli\n")):
            assert iv._resolve_gh_token() == "ghp_gh_cli"

    def test_gh_cli_called_process_error_falls_through(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.setattr(iv, "_COPILOT_APPS_JSON", tmp_path / "nonexistent.json")
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "gh"),
        ):
            with pytest.raises(EnvironmentError, match="GH_TOKEN"):
                iv._resolve_gh_token()

    def test_raises_when_nothing_available(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.setattr(iv, "_COPILOT_APPS_JSON", tmp_path / "nonexistent.json")
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(EnvironmentError, match="GH_TOKEN"):
                iv._resolve_gh_token()

    def test_apps_json_bad_structure_falls_through(self, monkeypatch, tmp_path):
        """Malformed apps.json must not crash – fall through to next method."""
        monkeypatch.delenv("GH_TOKEN", raising=False)
        apps = tmp_path / "apps.json"
        apps.write_text("not valid json {{{")
        monkeypatch.setattr(iv, "_COPILOT_APPS_JSON", apps)
        with patch("subprocess.run", return_value=MagicMock(stdout="ghp_fallback\n")):
            assert iv._resolve_gh_token() == "ghp_fallback"


# ---------------------------------------------------------------------------
# _resolve_anthropic_key
# ---------------------------------------------------------------------------


class TestResolveAnthropicKey:
    def test_returns_key_when_set(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-key")
        assert iv._resolve_anthropic_key() == "sk-ant-key"

    def test_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "  sk-ant-key  ")
        assert iv._resolve_anthropic_key() == "sk-ant-key"

    def test_raises_when_not_set(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
            iv._resolve_anthropic_key()

    def test_raises_when_empty(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
        with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
            iv._resolve_anthropic_key()


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
        monkeypatch.setattr(iv, "_COPILOT_APPS_JSON", tmp_path / "nonexistent.json")
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(EnvironmentError, match="GH_TOKEN"):
                iv.invoke("do the thing")

    def test_claude_missing_key_raises(self, monkeypatch):
        monkeypatch.setattr(iv, "_CLI", "claude")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
            iv.invoke("do the thing")
