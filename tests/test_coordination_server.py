"""Tests for CoordinationServer, models, FileBoardManager, and config."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml

# ── helpers ──────────────────────────────────────────────────────────────────


def _orc_dir(tmp_path: Path) -> Path:
    orc = tmp_path / ".orc"
    orc.mkdir(exist_ok=True)
    (orc / "work").mkdir(exist_ok=True)
    (orc / "vision").mkdir(exist_ok=True)
    (orc / "work" / "board.yaml").write_text("counter: 0\nopen: []\ndone: []\n")
    return orc


def _state(orc_dir: Path):
    """Return a StateManager rooted at *orc_dir* (already set up by _orc_dir)."""
    from orc.coordination.state import StateManager

    return StateManager(orc_dir)


class TestCoordinationServer:
    def test_stop_without_start_is_safe(self, tmp_path):
        """stop() when never started must not raise."""
        from orc.coordination.server import CoordinationServer
        from orc.coordination.state import StateManager

        orc = _orc_dir(tmp_path)
        server = CoordinationServer(StateManager(orc), tmp_path / "orc.sock")
        server.stop()  # should not raise

    def test_start_creates_socket_and_stop_removes_it(self, tmp_path):
        """Real server: socket appears on start, disappears on stop."""
        from orc.coordination.server import CoordinationServer
        from orc.coordination.state import StateManager

        orc = _orc_dir(tmp_path)
        sock = tmp_path / "orc.sock"
        server = CoordinationServer(StateManager(orc), sock)
        try:
            server.start()
            assert sock.exists()
        finally:
            server.stop()
        assert not sock.exists()

    def test_start_timeout_raises_runtime_error(self, tmp_path, monkeypatch):
        """If uvicorn never sets started=True within the timeout, RuntimeError is raised."""
        import orc.coordination.server as _srv_mod
        from orc.coordination.server import CoordinationServer
        from orc.coordination.state import StateManager

        # Reduce timeout and poll so the test is fast.
        monkeypatch.setattr(_srv_mod, "_STARTUP_TIMEOUT", 0.2)
        monkeypatch.setattr(_srv_mod, "_STARTUP_POLL", 0.05)

        class _NeverStartedServer:
            started = False
            should_exit = False

            def run(self) -> None:
                while not self.should_exit:
                    time.sleep(0.01)

        orc = _orc_dir(tmp_path)
        sock = tmp_path / "orc.sock"
        server = CoordinationServer(StateManager(orc), sock)

        # Patch uvicorn.Server so it never sets started=True.
        import uvicorn  # noqa: PLC0415

        monkeypatch.setattr(uvicorn, "Server", lambda cfg: _NeverStartedServer())

        with pytest.raises(RuntimeError, match="failed to start"):
            server.start()
        # Thread is cleaned up after timeout.
        if server._thread:
            server._thread.join(timeout=2.0)

    def test_start_idempotent_socket_cleanup(self, tmp_path):
        """A stale socket file at startup is silently removed before binding."""
        from orc.coordination.server import CoordinationServer
        from orc.coordination.state import StateManager

        orc = _orc_dir(tmp_path)
        sock = tmp_path / "orc.sock"
        sock.write_text("stale")  # simulate a stale socket
        server = CoordinationServer(StateManager(orc), sock)
        try:
            server.start()
            assert sock.exists()
        finally:
            server.stop()


# ─────────────────────────────────────────────────────────────────────────────
# Models tests
# ─────────────────────────────────────────────────────────────────────────────


class TestModels:
    def test_create_task_request(self):
        from orc.coordination.models import CreateTaskRequest

        m = CreateTaskRequest(title="foo-bar")
        assert m.title == "foo-bar"

    def test_create_task_response(self):
        from orc.coordination.models import CreateTaskResponse

        m = CreateTaskResponse(filename="0001-foo.md", path="/tmp/0001-foo.md")
        assert m.filename == "0001-foo.md"

    def test_set_status_request(self):
        from orc.coordination.models import SetStatusRequest

        m = SetStatusRequest(status="review")
        assert m.status == "review"

    def test_add_comment_request(self):
        from orc.coordination.models import AddCommentRequest

        m = AddCommentRequest(author="qa-1", text="looks good")
        assert m.author == "qa-1"

    def test_close_vision_request_defaults(self):
        from orc.coordination.models import CloseVisionRequest

        m = CloseVisionRequest(summary="done")
        assert m.task_files == []

    def test_task_entry_defaults(self):
        from orc.coordination.models import TaskEntry

        m = TaskEntry(name="0001-foo.md")
        assert m.status is None
        assert m.assigned_to is None
        assert m.comments == []

    def test_health_response(self):
        from orc.coordination.models import HealthResponse

        m = HealthResponse(status="ok", pid=1234)
        assert m.status == "ok"
        assert m.pid == 1234


# ─────────────────────────────────────────────────────────────────────────────
# FileBoardManager.create_task tests
# ─────────────────────────────────────────────────────────────────────────────


class TestFileBoardManagerCreateTask:
    def _mgr(self, tmp_path):
        from orc.board_manager import FileBoardManager

        orc = tmp_path / ".orc"
        orc.mkdir(exist_ok=True)
        (orc / "work").mkdir(exist_ok=True)
        (orc / "work" / "board.yaml").write_text("counter: 0\nopen: []\ndone: []\n")
        return FileBoardManager(orc)

    def test_create_task_returns_filename_and_path(self, tmp_path):
        mgr = self._mgr(tmp_path)
        filename, path = mgr.create_task("add-auth")
        assert filename == "0000-add-auth.md"
        assert path.exists()

    def test_create_task_increments_counter(self, tmp_path):
        mgr = self._mgr(tmp_path)
        mgr.create_task("first")
        f2, _ = mgr.create_task("second")
        assert f2.startswith("0001-")

    def test_create_task_adds_planned_entry_to_board(self, tmp_path):
        orc = tmp_path / ".orc"
        mgr = self._mgr(tmp_path)
        mgr.create_task("my-task")
        board = yaml.safe_load((orc / "work" / "board.yaml").read_text())
        assert board["open"][0]["status"] == "planned"


# ─────────────────────────────────────────────────────────────────────────────
# Config: api_socket_path
# ─────────────────────────────────────────────────────────────────────────────


class TestConfigApiSocketPath:
    def test_api_socket_path_is_set(self, tmp_path, _init_config):
        import orc.config as _cfg

        cfg = _cfg.get()
        assert cfg.api_socket_path == cfg.orc_dir / "run" / "orc.sock"
