"""Tests for orc/backends.py — AI CLI backends and protocol."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from orc.ai.backends import (
    SUPPORTED_BACKENDS,
    ClaudeBackend,
    CopilotBackend,
    SpawnResult,
    get_backend,
)
from orc.squad import PermissionConfig


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
        perm = PermissionConfig(mode="yolo")
        cmd = b._build_command("/tmp/ctx.txt", None, None, perm)
        assert cmd == ["copilot", "--yolo", "--prompt", "@/tmp/ctx.txt"]

    def test_build_command_confined(self):
        b = CopilotBackend()
        perm = PermissionConfig(
            mode="confined",
            allow_tools=("orc", "read", "write"),
            deny_tools=("shell(git push:*)",),
        )
        cmd = b._build_command("/tmp/ctx.txt", None, "/tmp/mcp.json", perm)
        assert "--allow-tool=orc" in cmd
        assert "--allow-tool=read" in cmd
        assert "--allow-tool=write" in cmd
        assert "--deny-tool=shell(git push:*)" in cmd
        assert "--additional-mcp-config" in cmd
        assert "@/tmp/mcp.json" in cmd
        assert "--prompt" in cmd
        assert "@/tmp/ctx.txt" in cmd
        assert "--yolo" not in cmd

    def test_build_command_confined_no_mcp(self):
        """In yolo mode the mcp config is NOT passed."""
        b = CopilotBackend()
        perm = PermissionConfig(mode="yolo")
        cmd = b._build_command("/tmp/ctx.txt", None, "/tmp/mcp.json", perm)
        assert "--additional-mcp-config" not in cmd

    def test_invoke_calls_subprocess(self, monkeypatch, tmp_path):
        b = CopilotBackend()
        monkeypatch.setenv("GH_TOKEN", "ghp_test")
        fake_result = MagicMock(returncode=0)
        with patch("subprocess.run", return_value=fake_result) as mock_run:
            rc = b.invoke("hello context", cwd=tmp_path, permissions=PermissionConfig(mode="yolo"))
        assert rc == 0
        assert mock_run.called

    def test_spawn_returns_popen_and_fh(self, monkeypatch, tmp_path):
        b = CopilotBackend()
        monkeypatch.setenv("GH_TOKEN", "ghp_test")
        log_path = tmp_path / "agent.log"
        fake_proc = MagicMock()
        with patch("subprocess.Popen", return_value=fake_proc):
            result = b.spawn(
                "context", tmp_path, log_path=log_path, permissions=PermissionConfig(mode="yolo")
            )
        assert isinstance(result, SpawnResult)
        assert result.process is fake_proc
        assert result.log_fh is not None
        result.log_fh.close()

    def test_spawn_without_log_returns_none_fh(self, monkeypatch, tmp_path):
        b = CopilotBackend()
        monkeypatch.setenv("GH_TOKEN", "ghp_test")
        fake_proc = MagicMock()
        with patch("subprocess.Popen", return_value=fake_proc):
            result = b.spawn("context", tmp_path, permissions=PermissionConfig(mode="yolo"))
        assert isinstance(result, SpawnResult)
        assert result.log_fh is None


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
        perm = PermissionConfig(mode="yolo")
        cmd = b._build_command("/tmp/ctx.txt", None, None, perm)
        assert cmd == ["claude", "-p", "@/tmp/ctx.txt", "--dangerouslySkipPermissions"]

    def test_build_command_with_model(self):
        b = ClaudeBackend()
        perm = PermissionConfig(mode="yolo")
        cmd = b._build_command("/tmp/ctx.txt", "claude-3-opus", None, perm)
        assert cmd == [
            "claude",
            "-p",
            "@/tmp/ctx.txt",
            "--model",
            "claude-3-opus",
            "--dangerouslySkipPermissions",
        ]

    def test_build_command_confined(self):
        b = ClaudeBackend()
        perm = PermissionConfig(
            mode="confined",
            allow_tools=("orc", "read", "write", "shell(git:*)"),
        )
        cmd = b._build_command("/tmp/ctx.txt", None, "/tmp/mcp.json", perm)
        assert "--mcp-config" in cmd
        assert "/tmp/mcp.json" in cmd
        assert "--allowedTools" in cmd
        assert "mcp__orc__*" in cmd
        assert "Read" in cmd
        assert "Write" in cmd
        assert "Bash(git *)" in cmd
        assert "--dangerouslySkipPermissions" not in cmd

    def test_build_command_confined_no_mcp_in_yolo(self):
        b = ClaudeBackend()
        perm = PermissionConfig(mode="yolo")
        cmd = b._build_command("/tmp/ctx.txt", None, "/tmp/mcp.json", perm)
        assert "--mcp-config" not in cmd

    def test_invoke_returns_exit_code(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        b = ClaudeBackend()
        with patch("subprocess.run", return_value=MagicMock(returncode=1)):
            rc = b.invoke("context", cwd=tmp_path, permissions=PermissionConfig(mode="yolo"))
        assert rc == 1

    def test_invoke_generates_mcp_config_when_confined(self, monkeypatch, tmp_path):
        """invoke creates and cleans up an MCP config file for non-yolo permissions."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("ORC_API_SOCKET", "/tmp/fake.sock")
        b = ClaudeBackend()
        perm = PermissionConfig(mode="confined", allow_tools=("orc",))
        created_files: list[str] = []

        original_build = b._build_command

        def tracking_build(prompt_file, model, mcp_config, permissions):
            if mcp_config:
                created_files.append(mcp_config)
            return original_build(prompt_file, model, mcp_config, permissions)

        b._build_command = tracking_build
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            rc = b.invoke(
                "context",
                cwd=tmp_path,
                agent_id="coder-1",
                role="coder",
                permissions=perm,
            )
        assert rc == 0
        assert len(created_files) == 1
        # MCP config should have been cleaned up
        from pathlib import Path

        assert not Path(created_files[0]).exists()

    def test_permission_flags_empty_when_no_allow_tools(self):
        """_permission_flags returns [] for confined mode with no allow_tools."""
        b = ClaudeBackend()
        perm = PermissionConfig(mode="confined")
        assert b._permission_flags(perm) == []
