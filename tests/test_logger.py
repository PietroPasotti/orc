"""Tests for orc/logger.py."""

import orc.logger as _log


class TestLoggerCoverage:
    def test_setup_json_format(self, tmp_path):
        """Lines 56, 60: JSON renderer branch."""
        _log.setup(log_format="json", log_level="warning", log_file=None)

    def test_setup_with_log_file(self, tmp_path):
        """Lines 66, 82-83: FileHandler branch."""
        log_file = tmp_path / "orc.log"
        _log.setup(log_format="text", log_level="debug", log_file=log_file)
        assert log_file.exists()

    def test_setup_orc_log_file_env(self, tmp_path, monkeypatch):
        """Line 60: ORC_LOG_FILE env var resolved."""
        log_file = tmp_path / "env.log"
        monkeypatch.setenv("ORC_LOG_FILE", str(log_file))
        _log.setup()
        assert log_file.exists()
        monkeypatch.delenv("ORC_LOG_FILE")

    def test_setup_orc_log_file_empty_string(self, tmp_path, monkeypatch):
        """ORC_LOG_FILE="" → resolved_log_file=None → no file created."""
        monkeypatch.setenv("ORC_LOG_FILE", "")
        _log.setup()
        monkeypatch.delenv("ORC_LOG_FILE")

    def test_setup_orc_log_dir(self, tmp_path, monkeypatch):
        """ORC_LOG_DIR set, ORC_LOG_FILE unset → log file is $ORC_LOG_DIR/orc.log."""
        monkeypatch.delenv("ORC_LOG_FILE", raising=False)
        monkeypatch.setenv("ORC_LOG_DIR", str(tmp_path))
        _log.setup()
        assert (tmp_path / "orc.log").exists()

    def test_setup_orc_log_file_takes_precedence_over_log_dir(self, tmp_path, monkeypatch):
        """ORC_LOG_FILE overrides ORC_LOG_DIR when both are set."""
        log_file = tmp_path / "explicit.log"
        log_dir = tmp_path / "dir"
        log_dir.mkdir(exist_ok=True)
        monkeypatch.setenv("ORC_LOG_FILE", str(log_file))
        monkeypatch.setenv("ORC_LOG_DIR", str(log_dir))
        _log.setup()
        assert log_file.exists()
        assert not (log_dir / "orc.log").exists()

    def test_setup_default_path_when_neither_set(self, monkeypatch):
        """Neither ORC_LOG_FILE nor ORC_LOG_DIR → default path is used."""
        monkeypatch.delenv("ORC_LOG_FILE", raising=False)
        monkeypatch.delenv("ORC_LOG_DIR", raising=False)
        # Just ensure setup() runs without error; default path creation is
        # a side effect we don't want to assert on in unit tests.
        _log.setup(log_file=None)

    def test_setup_default_log_file_param_used_when_no_env_set(self, tmp_path, monkeypatch):
        """default_log_file is used as fallback when ORC_LOG_FILE and ORC_LOG_DIR are unset."""
        monkeypatch.delenv("ORC_LOG_FILE", raising=False)
        monkeypatch.delenv("ORC_LOG_DIR", raising=False)
        log_file = tmp_path / "orc.log"
        _log.setup(default_log_file=log_file)
        assert log_file.exists()

    def test_setup_env_log_file_takes_precedence_over_default_log_file(self, tmp_path, monkeypatch):
        """ORC_LOG_FILE overrides default_log_file."""
        env_file = tmp_path / "from_env.log"
        default_file = tmp_path / "default.log"
        monkeypatch.setenv("ORC_LOG_FILE", str(env_file))
        monkeypatch.delenv("ORC_LOG_DIR", raising=False)
        _log.setup(default_log_file=default_file)
        assert env_file.exists()
        assert not default_file.exists()
