"""Tests for orc/config.py."""

from pathlib import Path
from unittest.mock import patch

import orc.config as _cfg


class TestConfigCoverage:
    def test_find_config_dir_uses_orc_dir_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ORC_DIR", str(tmp_path))
        result = _cfg._find_config_dir()
        assert result == tmp_path.resolve()
        monkeypatch.delenv("ORC_DIR")

    def test_find_config_dir_finds_orc_subdir(self, tmp_path):
        orc_dir = tmp_path / "orc"
        orc_dir.mkdir()
        result = _cfg._find_config_dir(base=tmp_path)
        assert result == orc_dir

    def test_validate_env_missing_env_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_cfg, "ENV_FILE", tmp_path / "nonexistent.env")
        errors = _cfg.validate_env()
        assert any(".env not found" in e for e in errors)

    def test_validate_env_all_vars_set(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("")
        monkeypatch.setattr(_cfg, "ENV_FILE", env_file)
        monkeypatch.setenv("COLONY_TELEGRAM_TOKEN", "tok123")
        monkeypatch.setenv("COLONY_TELEGRAM_CHAT_ID", "456")
        monkeypatch.setenv("COLONY_AI_CLI", "copilot")
        monkeypatch.setenv("GH_TOKEN", "ghp_abc")
        errors = _cfg.validate_env()
        assert not errors

    def test_validate_env_missing_vars(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("")
        monkeypatch.setattr(_cfg, "ENV_FILE", env_file)
        monkeypatch.delenv("COLONY_TELEGRAM_TOKEN", raising=False)
        monkeypatch.delenv("COLONY_TELEGRAM_CHAT_ID", raising=False)
        monkeypatch.delenv("COLONY_AI_CLI", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        errors = _cfg.validate_env()
        assert not any("COLONY_TELEGRAM_TOKEN" in e for e in errors), "Telegram is optional"
        assert any("COLONY_AI_CLI" in e for e in errors)

    def test_validate_env_unsupported_ai_cli(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("")
        monkeypatch.setattr(_cfg, "ENV_FILE", env_file)
        monkeypatch.setenv("COLONY_TELEGRAM_TOKEN", "tok")
        monkeypatch.setenv("COLONY_TELEGRAM_CHAT_ID", "123")
        monkeypatch.setenv("COLONY_AI_CLI", "gpt")
        errors = _cfg.validate_env()
        assert any("not supported" in e for e in errors)

    def test_validate_env_claude_missing_key(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("")
        monkeypatch.setattr(_cfg, "ENV_FILE", env_file)
        monkeypatch.setenv("COLONY_TELEGRAM_TOKEN", "tok")
        monkeypatch.setenv("COLONY_TELEGRAM_CHAT_ID", "123")
        monkeypatch.setenv("COLONY_AI_CLI", "claude")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        errors = _cfg.validate_env()
        assert any("ANTHROPIC_API_KEY" in e for e in errors)

    def test_validate_env_copilot_no_token_no_apps_json_no_gh_cli(self, tmp_path, monkeypatch):
        """No GH_TOKEN, no apps.json, gh auth token fails → error."""
        env_file = tmp_path / ".env"
        env_file.write_text("")
        monkeypatch.setattr(_cfg, "ENV_FILE", env_file)
        monkeypatch.setenv("COLONY_TELEGRAM_TOKEN", "tok")
        monkeypatch.setenv("COLONY_TELEGRAM_CHAT_ID", "123")
        monkeypatch.setenv("COLONY_AI_CLI", "copilot")
        monkeypatch.delenv("GH_TOKEN", raising=False)
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        with patch("orc.config.Path") as mock_path_cls:
            real_path = Path
            mock_path_cls.side_effect = lambda *a, **kw: real_path(*a, **kw)
            mock_path_cls.home.return_value = fake_home
            with patch("orc.config.subprocess.run", side_effect=FileNotFoundError):
                errors = _cfg.validate_env()
        assert any("GitHub" in e for e in errors)

    def test_validate_env_apps_json_malformed_swallows_exception(self, tmp_path, monkeypatch):
        """Lines 140-141: apps.json empty dict → StopIteration caught silently."""
        env_file = tmp_path / ".env"
        env_file.write_text("")
        monkeypatch.setattr(_cfg, "ENV_FILE", env_file)
        monkeypatch.setenv("COLONY_TELEGRAM_TOKEN", "tok")
        monkeypatch.setenv("COLONY_TELEGRAM_CHAT_ID", "123")
        monkeypatch.setenv("COLONY_AI_CLI", "copilot")
        monkeypatch.delenv("GH_TOKEN", raising=False)
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        apps_dir = fake_home / ".config" / "github-copilot"
        apps_dir.mkdir(parents=True)
        (apps_dir / "apps.json").write_text("{}")  # empty dict → next(iter({})) raises
        with patch("orc.config.Path") as mock_path_cls:
            real_path = Path
            mock_path_cls.side_effect = lambda *a, **kw: real_path(*a, **kw)
            mock_path_cls.home.return_value = fake_home
            with patch("orc.config.subprocess.run", side_effect=FileNotFoundError):
                errors = _cfg.validate_env()
        assert any("GitHub" in e for e in errors)

    def test_validate_env_empty_gh_auth_token(self, tmp_path, monkeypatch):
        """Lines 147-148: gh auth token returns empty string → error added."""
        env_file = tmp_path / ".env"
        env_file.write_text("")
        monkeypatch.setattr(_cfg, "ENV_FILE", env_file)
        monkeypatch.setenv("COLONY_TELEGRAM_TOKEN", "tok")
        monkeypatch.setenv("COLONY_TELEGRAM_CHAT_ID", "123")
        monkeypatch.setenv("COLONY_AI_CLI", "copilot")
        monkeypatch.delenv("GH_TOKEN", raising=False)
        fake_home = tmp_path / "home2"
        fake_home.mkdir()

        def fake_run(cmd, **kwargs):
            from unittest.mock import MagicMock

            r = MagicMock()
            r.stdout = ""
            r.returncode = 0
            return r

        with patch("orc.config.Path") as mock_path_cls:
            real_path = Path
            mock_path_cls.side_effect = lambda *a, **kw: real_path(*a, **kw)
            mock_path_cls.home.return_value = fake_home
            with patch("orc.config.subprocess.run", fake_run):
                errors = _cfg.validate_env()
        assert any("GitHub" in e or "token" in e.lower() for e in errors)

    def test_validate_env_copilot_gh_token_ok(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("")
        monkeypatch.setattr(_cfg, "ENV_FILE", env_file)
        monkeypatch.setenv("COLONY_TELEGRAM_TOKEN", "tok")
        monkeypatch.setenv("COLONY_TELEGRAM_CHAT_ID", "123")
        monkeypatch.setenv("COLONY_AI_CLI", "copilot")
        monkeypatch.setenv("GH_TOKEN", "ghp_token")
        errors = _cfg.validate_env()
        assert not [e for e in errors if "GitHub" in e]
