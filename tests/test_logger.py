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
