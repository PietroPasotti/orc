"""Tests for orc/backends.py — AI CLI backends and protocol."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from orc.ai.backends import (
    SUPPORTED_BACKENDS,
    AIBackend,
    ClaudeBackend,
    CopilotBackend,
    get_backend,
)


class TestGetBackend:
    def test_returns_copilot_backend(self):
        b = get_backend("copilot")
        assert isinstance(b, CopilotBackend)

    def test_returns_claude_backend(self):
        b = get_backend("claude")
        assert isinstance(b, ClaudeBackend)

    def test_unsupported_raises(self):
        with pytest.raises(OSError, match="not supported"):
            get_backend("gpt-4")

    def test_supported_backends_set(self):
        assert "copilot" in SUPPORTED_BACKENDS
        assert "claude" in SUPPORTED_BACKENDS


class TestCopilotBackend:
    def test_name(self):
        assert CopilotBackend().name == "copilot"

    def test_resolve_token_from_env(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "ghp_env_token")
        b = CopilotBackend()
        assert b.resolve_token() == "ghp_env_token"

    def test_resolve_token_from_apps_json(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GH_TOKEN", raising=False)
        apps = tmp_path / "apps.json"
        apps.write_text(json.dumps({"entry": {"oauth_token": "ghp_apps"}}))
        b = CopilotBackend()
        b.APPS_JSON = apps
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert b.resolve_token() == "ghp_apps"

    def test_resolve_token_from_gh_cli(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GH_TOKEN", raising=False)
        b = CopilotBackend()
        b.APPS_JSON = tmp_path / "nonexistent.json"
        with patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout="ghp_cli\n"),
        ):
            assert b.resolve_token() == "ghp_cli"

    def test_resolve_token_raises_when_nothing_available(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GH_TOKEN", raising=False)
        b = CopilotBackend()
        b.APPS_JSON = tmp_path / "nonexistent.json"
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(OSError, match="GH_TOKEN"):
                b.resolve_token()

    def test_resolve_token_gh_cli_error_falls_through(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GH_TOKEN", raising=False)
        b = CopilotBackend()
        b.APPS_JSON = tmp_path / "nonexistent.json"
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "gh"),
        ):
            with pytest.raises(OSError, match="GH_TOKEN"):
                b.resolve_token()

    def test_resolve_token_apps_json_bad_json(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GH_TOKEN", raising=False)
        apps = tmp_path / "apps.json"
        apps.write_text("not json {{{")
        b = CopilotBackend()
        b.APPS_JSON = apps
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(OSError):
                b.resolve_token()

    def test_build_command(self):
        b = CopilotBackend()
        cmd = b._build_command("/tmp/ctx.txt", None)
        assert cmd == ["copilot", "--yolo", "--prompt", "@/tmp/ctx.txt"]

    def test_invoke_calls_subprocess(self, monkeypatch, tmp_path):
        b = CopilotBackend()
        monkeypatch.setenv("GH_TOKEN", "ghp_test")
        fake_result = MagicMock(returncode=0)
        with patch("subprocess.run", return_value=fake_result) as mock_run:
            rc = b.invoke("hello context", cwd=tmp_path)
        assert rc == 0
        assert mock_run.called

    def test_spawn_returns_popen_and_fh(self, monkeypatch, tmp_path):
        b = CopilotBackend()
        monkeypatch.setenv("GH_TOKEN", "ghp_test")
        log_path = tmp_path / "agent.log"
        fake_proc = MagicMock()
        with patch("subprocess.Popen", return_value=fake_proc):
            proc, fh = b.spawn("context", tmp_path, log_path=log_path)
        assert proc is fake_proc
        assert fh is not None
        fh.close()

    def test_spawn_without_log_returns_none_fh(self, monkeypatch, tmp_path):
        b = CopilotBackend()
        monkeypatch.setenv("GH_TOKEN", "ghp_test")
        fake_proc = MagicMock()
        with patch("subprocess.Popen", return_value=fake_proc):
            proc, fh = b.spawn("context", tmp_path)
        assert fh is None


class TestClaudeBackend:
    def test_name(self):
        assert ClaudeBackend().name == "claude"

    def test_resolve_key_from_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        assert ClaudeBackend().resolve_key() == "sk-ant-test"

    def test_resolve_key_raises_when_missing(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(OSError, match="ANTHROPIC_API_KEY"):
            ClaudeBackend().resolve_key()

    def test_build_command_without_model(self):
        b = ClaudeBackend()
        cmd = b._build_command("/tmp/ctx.txt", None)
        assert cmd == ["claude", "-p", "@/tmp/ctx.txt"]

    def test_build_command_with_model(self):
        b = ClaudeBackend()
        cmd = b._build_command("/tmp/ctx.txt", "claude-3-opus")
        assert cmd == ["claude", "-p", "@/tmp/ctx.txt", "--model", "claude-3-opus"]

    def test_invoke_returns_exit_code(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        b = ClaudeBackend()
        with patch("subprocess.run", return_value=MagicMock(returncode=1)):
            rc = b.invoke("context", cwd=tmp_path)
        assert rc == 1


class TestAIBackendProtocol:
    """Verify that concrete backends satisfy the AIBackend protocol."""

    def test_copilot_satisfies_protocol(self):
        assert isinstance(CopilotBackend(), AIBackend)

    def test_claude_satisfies_protocol(self):
        assert isinstance(ClaudeBackend(), AIBackend)
