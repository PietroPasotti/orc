"""Tests for orc/config.py."""

from pathlib import Path
from unittest.mock import patch

import pytest

import orc.config as _cfg


def _setup_env_config(tmp_path, monkeypatch):
    """Helper: create env_file and initialize config for validate_env tests."""
    env_file = tmp_path / ".env"
    env_file.write_text("")
    orc_dir = tmp_path / ".orc"
    orc_dir.mkdir(exist_ok=True)
    _cfg.init(orc_dir)
    monkeypatch.setattr(
        _cfg, "_config", _cfg.Config(**{**_cfg.get().__dict__, "env_file": env_file})
    )
    return env_file


class TestConfigCoverage:
    def test_get_before_init_raises(self, monkeypatch):
        monkeypatch.setattr(_cfg, "_config", None)
        with pytest.raises(RuntimeError, match="init"):
            _cfg.get()

    def test_find_config_dir_uses_orc_dir_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORC_DIR", str(tmp_path))
        result = _cfg.find_config_dir()
        assert result == tmp_path.resolve()
        monkeypatch.delenv("ORC_DIR")

    def test_find_config_dir_finds_dot_orc_subdir(self, tmp_path):
        orc_dir = tmp_path / ".orc"
        orc_dir.mkdir(exist_ok=True)
        result = _cfg.find_config_dir(base=tmp_path)
        assert result == orc_dir

    def test_find_config_dir_returns_none_for_bare_orc(self, tmp_path):
        subdir = tmp_path / "project"
        subdir.mkdir(exist_ok=True)
        (subdir / "orc").mkdir(exist_ok=True)
        result = _cfg.find_config_dir(base=subdir)
        assert result is None

    def test_validate_env_missing_env_file(self, tmp_path, monkeypatch):
        orc_dir = tmp_path / ".orc"
        orc_dir.mkdir(exist_ok=True)
        _cfg.init(orc_dir)
        monkeypatch.setattr(
            _cfg,
            "_config",
            _cfg.Config(**{**_cfg.get().__dict__, "env_file": tmp_path / "nonexistent.env"}),
        )
        errors = _cfg.validate_env()
        assert any(".env not found" in e for e in errors)

    def test_validate_env_all_vars_set(self, tmp_path, monkeypatch):
        _setup_env_config(tmp_path, monkeypatch)
        monkeypatch.setenv("COLONY_TELEGRAM_TOKEN", "tok123")
        monkeypatch.setenv("COLONY_TELEGRAM_CHAT_ID", "456")
        monkeypatch.setenv("GEMINI_API_TOKEN", "test-key")
        errors = _cfg.validate_env()
        assert not errors

    def test_validate_env_missing_all_api_keys(self, tmp_path, monkeypatch):
        _setup_env_config(tmp_path, monkeypatch)
        monkeypatch.delenv("COLONY_TELEGRAM_TOKEN", raising=False)
        monkeypatch.delenv("COLONY_TELEGRAM_CHAT_ID", raising=False)
        monkeypatch.delenv("GEMINI_API_TOKEN", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        with patch("orc.config.subprocess.run", side_effect=FileNotFoundError):
            with patch("orc.config.Path") as mock_path_cls:
                real_path = Path
                mock_path_cls.side_effect = lambda *a, **kw: real_path(*a, **kw)
                mock_path_cls.home.return_value = tmp_path / "nohome"
                errors = _cfg.validate_env()
        assert not any("COLONY_TELEGRAM_TOKEN" in e for e in errors), "Telegram is optional"
        assert any("LLM API key" in e for e in errors)

    def test_validate_env_gemini_key_sufficient(self, tmp_path, monkeypatch):
        _setup_env_config(tmp_path, monkeypatch)
        monkeypatch.setenv("GEMINI_API_TOKEN", "test-key")
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        errors = _cfg.validate_env()
        assert not any("LLM API key" in e for e in errors)

    def test_validate_env_openai_key_sufficient(self, tmp_path, monkeypatch):
        _setup_env_config(tmp_path, monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("GEMINI_API_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        errors = _cfg.validate_env()
        assert not any("LLM API key" in e for e in errors)

    def test_validate_env_gh_token_sufficient(self, tmp_path, monkeypatch):
        _setup_env_config(tmp_path, monkeypatch)
        monkeypatch.setenv("GH_TOKEN", "ghp_test")
        monkeypatch.delenv("GEMINI_API_TOKEN", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        errors = _cfg.validate_env()
        assert not any("LLM API key" in e for e in errors)

    def test_validate_env_no_keys_but_apps_json(self, tmp_path, monkeypatch):
        """apps.json has a valid oauth_token → no API key error."""
        _setup_env_config(tmp_path, monkeypatch)
        monkeypatch.delenv("GEMINI_API_TOKEN", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        fake_home = tmp_path / "home3"
        fake_home.mkdir(exist_ok=True)
        apps_dir = fake_home / ".config" / "github-copilot"
        apps_dir.mkdir(parents=True, exist_ok=True)
        (apps_dir / "apps.json").write_text('{"device": {"oauth_token": "ghu_abc123"}}')
        with patch("orc.config.Path") as mock_path_cls:
            real_path = Path
            mock_path_cls.side_effect = lambda *a, **kw: real_path(*a, **kw)
            mock_path_cls.home.return_value = fake_home
            errors = _cfg.validate_env()
        assert not any("LLM API key" in e for e in errors)


class TestLoadOrcConfig:
    def test_returns_empty_dict_when_no_config_file(self, tmp_path):
        from orc.config import OrcConfig

        result = _cfg.load_orc_config(tmp_path)
        assert result == OrcConfig()

    def test_reads_orc_dev_branch(self, tmp_path):
        (tmp_path / "config.yaml").write_text("orc-dev-branch: staging\n")
        result = _cfg.load_orc_config(tmp_path)
        from orc.config import OrcConfig

        assert result == OrcConfig(orc_dev_branch="staging")

    def test_returns_empty_dict_on_malformed_yaml(self, tmp_path):
        from orc.config import OrcConfig

        (tmp_path / "config.yaml").write_text(": [\ninvalid yaml{{{")
        result = _cfg.load_orc_config(tmp_path)
        assert result == OrcConfig()

    def test_init_sets_work_dev_branch_from_config(self, tmp_path, monkeypatch, _init_config):
        orc_dir = tmp_path / ".orc"
        orc_dir.mkdir(exist_ok=True)
        (orc_dir / "config.yaml").write_text("orc-dev-branch: my-dev\n")
        _init_config(orc_dir)
        assert _cfg.get().work_dev_branch == "my-dev"

    def test_init_defaults_work_dev_branch_to_dev(self, tmp_path, monkeypatch, _init_config):
        orc_dir = tmp_path / ".orc"
        orc_dir.mkdir(exist_ok=True)
        _init_config(orc_dir)
        assert _cfg.get().work_dev_branch == "dev"

    def test_init_sets_branch_prefix_from_config(self, tmp_path, monkeypatch, _init_config):
        orc_dir = tmp_path / ".orc"
        orc_dir.mkdir(exist_ok=True)
        (orc_dir / "config.yaml").write_text("orc-branch-prefix: orc\n")
        _init_config(orc_dir)
        assert _cfg.get().branch_prefix == "orc"

    def test_init_defaults_branch_prefix_to_empty_string(self, tmp_path, monkeypatch, _init_config):
        orc_dir = tmp_path / ".orc"
        orc_dir.mkdir(exist_ok=True)
        _init_config(orc_dir)
        assert _cfg.get().branch_prefix == ""

    def test_init_sets_worktree_base_from_config(self, tmp_path, monkeypatch, _init_config):
        orc_dir = tmp_path / ".orc"
        orc_dir.mkdir(exist_ok=True)
        custom_base = tmp_path / "my-worktrees"
        (orc_dir / "config.yaml").write_text(f"orc-worktree-base: {custom_base}\n")
        _init_config(orc_dir)
        assert _cfg.get().worktree_base == custom_base.resolve()

    def test_init_defaults_worktree_base_to_orc_worktrees(
        self, tmp_path, monkeypatch, _init_config
    ):
        orc_dir = tmp_path / ".orc"
        orc_dir.mkdir(exist_ok=True)
        _init_config(orc_dir)
        assert _cfg.get().worktree_base == (orc_dir / "worktrees").resolve()

    def test_init_dev_worktree_under_worktree_base(self, tmp_path, monkeypatch, _init_config):
        orc_dir = tmp_path / ".orc"
        orc_dir.mkdir(exist_ok=True)
        custom_base = tmp_path / "wt"
        (orc_dir / "config.yaml").write_text(
            f"orc-worktree-base: {custom_base}\norc-dev-branch: staging\n"
        )
        _init_config(orc_dir, repo_root=tmp_path)
        assert _cfg.get().dev_worktree == custom_base.resolve() / "staging"

    def test_init_log_dir_defaults_to_orc_logs(self, tmp_path, monkeypatch, _init_config):
        """LOG_DIR defaults to .orc/logs/ when orc-log-dir is not in config."""
        orc_dir = tmp_path / ".orc"
        orc_dir.mkdir(exist_ok=True)
        _init_config(orc_dir)
        assert _cfg.get().log_dir == (orc_dir / "logs").resolve()

    def test_init_log_dir_from_config(self, tmp_path, monkeypatch, _init_config):
        """LOG_DIR is set from orc-log-dir in config.yaml."""
        orc_dir = tmp_path / ".orc"
        orc_dir.mkdir(exist_ok=True)
        custom_log_dir = tmp_path / "my-logs"
        (orc_dir / "config.yaml").write_text(f"orc-log-dir: {custom_log_dir}\n")
        _init_config(orc_dir)
        assert _cfg.get().log_dir == custom_log_dir.resolve()

    def test_init_todo_scan_exclude_defaults_to_orc(self, tmp_path, monkeypatch, _init_config):
        """todo_scan_exclude defaults to ('.orc',) when orc-todo-scan-exclude is absent."""
        orc_dir = tmp_path / ".orc"
        orc_dir.mkdir(exist_ok=True)
        _init_config(orc_dir)
        assert _cfg.get().todo_scan_exclude == (".orc",)

    def test_init_todo_scan_exclude_from_config(self, tmp_path, monkeypatch, _init_config):
        """todo_scan_exclude is read from orc-todo-scan-exclude in config.yaml."""
        orc_dir = tmp_path / ".orc"
        orc_dir.mkdir(exist_ok=True)
        (orc_dir / "config.yaml").write_text(
            "orc-todo-scan-exclude:\n  - .orc\n  - vendor\n  - docs\n"
        )
        _init_config(orc_dir)
        assert _cfg.get().todo_scan_exclude == (".orc", "vendor", "docs")

    def test_init_chat_log_in_log_dir(self, tmp_path, monkeypatch, _init_config):
        """chat_log is placed inside log_dir, not orc_dir."""
        orc_dir = tmp_path / ".orc"
        orc_dir.mkdir(exist_ok=True)
        _init_config(orc_dir)
        cfg = _cfg.get()
        assert cfg.chat_log == cfg.log_dir / "chat.log"

    def test_init_chat_log_uses_custom_log_dir(self, tmp_path, monkeypatch, _init_config):
        """chat_log follows a custom orc-log-dir."""
        orc_dir = tmp_path / ".orc"
        orc_dir.mkdir(exist_ok=True)
        custom_log_dir = tmp_path / "my-logs"
        (orc_dir / "config.yaml").write_text(f"orc-log-dir: {custom_log_dir}\n")
        _init_config(orc_dir)
        assert _cfg.get().chat_log == custom_log_dir.resolve() / "chat.log"
