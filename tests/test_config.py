"""Tests for orc/config.py."""

from pathlib import Path
from unittest.mock import patch

import pytest

import orc.config as _cfg


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
        agents_dir = tmp_path / ".orc"
        agents_dir.mkdir(exist_ok=True)
        _cfg.init(agents_dir)
        monkeypatch.setattr(
            _cfg,
            "_config",
            _cfg.Config(**{**_cfg.get().__dict__, "env_file": tmp_path / "nonexistent.env"}),
        )
        errors = _cfg.validate_env()
        assert any(".env not found" in e for e in errors)

    def test_validate_env_all_vars_set(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("")
        agents_dir = tmp_path / ".orc"
        agents_dir.mkdir(exist_ok=True)
        _cfg.init(agents_dir)
        monkeypatch.setattr(
            _cfg, "_config", _cfg.Config(**{**_cfg.get().__dict__, "env_file": env_file})
        )
        monkeypatch.setenv("COLONY_TELEGRAM_TOKEN", "tok123")
        monkeypatch.setenv("COLONY_TELEGRAM_CHAT_ID", "456")
        monkeypatch.setenv("COLONY_AI_CLI", "copilot")
        monkeypatch.setenv("GH_TOKEN", "ghp_abc")
        errors = _cfg.validate_env()
        assert not errors

    def test_validate_env_missing_vars(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("")
        agents_dir = tmp_path / ".orc"
        agents_dir.mkdir(exist_ok=True)
        _cfg.init(agents_dir)
        monkeypatch.setattr(
            _cfg, "_config", _cfg.Config(**{**_cfg.get().__dict__, "env_file": env_file})
        )
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
        agents_dir = tmp_path / ".orc"
        agents_dir.mkdir(exist_ok=True)
        _cfg.init(agents_dir)
        monkeypatch.setattr(
            _cfg, "_config", _cfg.Config(**{**_cfg.get().__dict__, "env_file": env_file})
        )
        monkeypatch.setenv("COLONY_TELEGRAM_TOKEN", "tok")
        monkeypatch.setenv("COLONY_TELEGRAM_CHAT_ID", "123")
        monkeypatch.setenv("COLONY_AI_CLI", "gpt")
        errors = _cfg.validate_env()
        assert any("not supported" in e for e in errors)

    def test_validate_env_claude_missing_key(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("")
        agents_dir = tmp_path / ".orc"
        agents_dir.mkdir(exist_ok=True)
        _cfg.init(agents_dir)
        monkeypatch.setattr(
            _cfg, "_config", _cfg.Config(**{**_cfg.get().__dict__, "env_file": env_file})
        )
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
        agents_dir = tmp_path / ".orc"
        agents_dir.mkdir(exist_ok=True)
        _cfg.init(agents_dir)
        monkeypatch.setattr(
            _cfg, "_config", _cfg.Config(**{**_cfg.get().__dict__, "env_file": env_file})
        )
        monkeypatch.setenv("COLONY_TELEGRAM_TOKEN", "tok")
        monkeypatch.setenv("COLONY_TELEGRAM_CHAT_ID", "123")
        monkeypatch.setenv("COLONY_AI_CLI", "copilot")
        monkeypatch.delenv("GH_TOKEN", raising=False)
        fake_home = tmp_path / "home"
        fake_home.mkdir(exist_ok=True)
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
        agents_dir = tmp_path / ".orc"
        agents_dir.mkdir(exist_ok=True)
        _cfg.init(agents_dir)
        monkeypatch.setattr(
            _cfg, "_config", _cfg.Config(**{**_cfg.get().__dict__, "env_file": env_file})
        )
        monkeypatch.setenv("COLONY_TELEGRAM_TOKEN", "tok")
        monkeypatch.setenv("COLONY_TELEGRAM_CHAT_ID", "123")
        monkeypatch.setenv("COLONY_AI_CLI", "copilot")
        monkeypatch.delenv("GH_TOKEN", raising=False)
        fake_home = tmp_path / "home"
        fake_home.mkdir(exist_ok=True)
        apps_dir = fake_home / ".config" / "github-copilot"
        apps_dir.mkdir(parents=True, exist_ok=True)
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
        agents_dir = tmp_path / ".orc"
        agents_dir.mkdir(exist_ok=True)
        _cfg.init(agents_dir)
        monkeypatch.setattr(
            _cfg, "_config", _cfg.Config(**{**_cfg.get().__dict__, "env_file": env_file})
        )
        monkeypatch.setenv("COLONY_TELEGRAM_TOKEN", "tok")
        monkeypatch.setenv("COLONY_TELEGRAM_CHAT_ID", "123")
        monkeypatch.setenv("COLONY_AI_CLI", "copilot")
        monkeypatch.delenv("GH_TOKEN", raising=False)
        fake_home = tmp_path / "home2"
        fake_home.mkdir(exist_ok=True)

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

    def test_validate_env_apps_json_with_oauth_token(self, tmp_path, monkeypatch):
        """Line 134: apps.json has a valid oauth_token → no GitHub error."""
        env_file = tmp_path / ".env"
        env_file.write_text("")
        agents_dir = tmp_path / ".orc"
        agents_dir.mkdir(exist_ok=True)
        _cfg.init(agents_dir)
        monkeypatch.setattr(
            _cfg, "_config", _cfg.Config(**{**_cfg.get().__dict__, "env_file": env_file})
        )
        monkeypatch.setenv("COLONY_AI_CLI", "copilot")
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
        assert not any("GitHub" in e for e in errors)

    def test_validate_env_copilot_gh_token_ok(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("")
        agents_dir = tmp_path / ".orc"
        agents_dir.mkdir(exist_ok=True)
        _cfg.init(agents_dir)
        monkeypatch.setattr(
            _cfg, "_config", _cfg.Config(**{**_cfg.get().__dict__, "env_file": env_file})
        )
        monkeypatch.setenv("COLONY_TELEGRAM_TOKEN", "tok")
        monkeypatch.setenv("COLONY_TELEGRAM_CHAT_ID", "123")
        monkeypatch.setenv("COLONY_AI_CLI", "copilot")
        monkeypatch.setenv("GH_TOKEN", "ghp_token")
        errors = _cfg.validate_env()
        assert not [e for e in errors if "GitHub" in e]


class TestLoadOrcConfig:
    def test_returns_empty_dict_when_no_config_file(self, tmp_path):
        result = _cfg.load_orc_config(tmp_path)
        assert result == {}

    def test_reads_orc_dev_branch(self, tmp_path):
        (tmp_path / "config.yaml").write_text("orc-dev-branch: staging\n")
        result = _cfg.load_orc_config(tmp_path)
        assert result == {"orc-dev-branch": "staging"}

    def test_returns_empty_dict_on_malformed_yaml(self, tmp_path):
        (tmp_path / "config.yaml").write_text(": [\ninvalid yaml{{{")
        result = _cfg.load_orc_config(tmp_path)
        assert result == {}

    def test_init_sets_work_dev_branch_from_config(self, tmp_path, monkeypatch, _init_config):
        agents_dir = tmp_path / ".orc"
        agents_dir.mkdir(exist_ok=True)
        (agents_dir / "config.yaml").write_text("orc-dev-branch: my-dev\n")
        _init_config(agents_dir)
        assert _cfg.get().work_dev_branch == "my-dev"

    def test_init_defaults_work_dev_branch_to_dev(self, tmp_path, monkeypatch, _init_config):
        agents_dir = tmp_path / ".orc"
        agents_dir.mkdir(exist_ok=True)
        _init_config(agents_dir)
        assert _cfg.get().work_dev_branch == "dev"

    def test_init_sets_branch_prefix_from_config(self, tmp_path, monkeypatch, _init_config):
        agents_dir = tmp_path / ".orc"
        agents_dir.mkdir(exist_ok=True)
        (agents_dir / "config.yaml").write_text("orc-branch-prefix: orc\n")
        _init_config(agents_dir)
        assert _cfg.get().branch_prefix == "orc"

    def test_init_defaults_branch_prefix_to_empty_string(self, tmp_path, monkeypatch, _init_config):
        agents_dir = tmp_path / ".orc"
        agents_dir.mkdir(exist_ok=True)
        _init_config(agents_dir)
        assert _cfg.get().branch_prefix == ""

    def test_init_sets_worktree_base_from_config(self, tmp_path, monkeypatch, _init_config):
        agents_dir = tmp_path / ".orc"
        agents_dir.mkdir(exist_ok=True)
        custom_base = tmp_path / "my-worktrees"
        (agents_dir / "config.yaml").write_text(f"orc-worktree-base: {custom_base}\n")
        _init_config(agents_dir)
        assert _cfg.get().worktree_base == custom_base.resolve()

    def test_init_defaults_worktree_base_to_orc_worktrees(
        self, tmp_path, monkeypatch, _init_config
    ):
        agents_dir = tmp_path / ".orc"
        agents_dir.mkdir(exist_ok=True)
        _init_config(agents_dir)
        assert _cfg.get().worktree_base == (agents_dir / "worktrees").resolve()

    def test_init_dev_worktree_under_worktree_base(self, tmp_path, monkeypatch, _init_config):
        agents_dir = tmp_path / ".orc"
        agents_dir.mkdir(exist_ok=True)
        custom_base = tmp_path / "wt"
        (agents_dir / "config.yaml").write_text(
            f"orc-worktree-base: {custom_base}\norc-dev-branch: staging\n"
        )
        _init_config(agents_dir, repo_root=tmp_path)
        assert _cfg.get().dev_worktree == custom_base.resolve() / "staging"

    def test_init_log_dir_defaults_to_orc_logs(self, tmp_path, monkeypatch, _init_config):
        """LOG_DIR defaults to .orc/logs/ when orc-log-dir is not in config."""
        agents_dir = tmp_path / ".orc"
        agents_dir.mkdir(exist_ok=True)
        _init_config(agents_dir)
        assert _cfg.get().log_dir == (agents_dir / "logs").resolve()

    def test_init_log_dir_from_config(self, tmp_path, monkeypatch, _init_config):
        """LOG_DIR is set from orc-log-dir in config.yaml."""
        agents_dir = tmp_path / ".orc"
        agents_dir.mkdir(exist_ok=True)
        custom_log_dir = tmp_path / "my-logs"
        (agents_dir / "config.yaml").write_text(f"orc-log-dir: {custom_log_dir}\n")
        _init_config(agents_dir)
        assert _cfg.get().log_dir == custom_log_dir.resolve()
