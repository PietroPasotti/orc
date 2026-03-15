"""Tests for the orc coordination package.

Coverage strategy
-----------------
* :class:`~orc.coordination.state.BoardStateManager` — direct instantiation, all
  public methods exercised including edge-cases (not-found, no-dir, etc.).
* Route handlers — called directly as plain Python functions by passing the
  ``BoardStateManager`` via the ``state=`` keyword argument (bypassing FastAPI DI
  so no HTTP transport or test-client is needed).
* :class:`~orc.coordination.server.CoordinationServer` — start/stop on a
  real temp Unix socket; the startup-timeout path is tested via monkeypatching.
* ``create_app`` — smoke-tested to confirm routers are mounted.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

# ── helpers ────────────────────────────────────────────────────────────────


def _orc_dir(tmp_path: Path) -> Path:
    orc = tmp_path / ".orc"
    orc.mkdir(exist_ok=True)
    (orc / "work").mkdir(exist_ok=True)
    (orc / "vision" / "ready").mkdir(parents=True, exist_ok=True)
    (orc / "work" / "board.yaml").write_text("counter: 0\ntasks: []\n")
    return orc


def _state(orc_dir: Path):
    """Return a BoardStateManager rooted at *orc_dir* (already set up by _orc_dir)."""
    from orc.coordination.state import BoardStateManager

    return BoardStateManager(orc_dir)


# ─────────────────────────────────────────────────────────────────────────────
# BoardStateManager tests
# ─────────────────────────────────────────────────────────────────────────────


class TestStateManagerBoardQueries:
    def test_get_open_tasks_empty(self, tmp_path):
        orc = _orc_dir(tmp_path)
        assert _state(orc).get_tasks() == []

    def test_get_open_tasks_wraps_strings(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "work" / "board.yaml").write_text("tasks:\n  - 0001-foo.md\n")
        result = _state(orc).get_tasks()
        assert len(result) == 1
        assert result[0].name == "0001-foo.md"

    def test_get_open_tasks_returns_dicts(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "work" / "board.yaml").write_text(
            "tasks:\n  - name: 0001-foo.md\n    status: in-progress\n"
        )
        result = _state(orc).get_tasks()
        assert result[0].name == "0001-foo.md"
        assert result[0].status == "in-progress"

    def test_get_task_found(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "work" / "board.yaml").write_text(
            "tasks:\n  - name: 0001-foo.md\n    status: planned\n"
        )
        t = _state(orc).get_task("0001-foo.md")
        assert t is not None
        assert t.name == "0001-foo.md"

    def test_get_task_not_found(self, tmp_path):
        orc = _orc_dir(tmp_path)
        assert _state(orc).get_task("9999-missing.md") is None

    def test_read_task_content_found(self, tmp_path):
        orc = _orc_dir(tmp_path)
        task_file = orc / "work" / "0001-foo.md"
        task_file.write_text("# Task 0001\n\nSome content.")
        content = _state(orc).read_task_content("0001-foo.md")
        assert content == "# Task 0001\n\nSome content."

    def test_read_task_content_not_found(self, tmp_path):
        orc = _orc_dir(tmp_path)
        with pytest.raises(FileNotFoundError):
            _state(orc).read_task_content("9999-missing.md")


class TestStateManagerBoardMutations:
    def test_create_task_creates_file_and_board_entry(self, tmp_path):
        orc = _orc_dir(tmp_path)
        s = _state(orc)
        from orc.coordination.models import TaskBody

        filename, path = s.create_task(
            "add-user-auth",
            "0001-feat.md",
            TaskBody(overview="x", in_scope=[], out_of_scope=[], steps=[]),
        )
        assert filename == "0000-add-user-auth.md"
        assert path.exists()
        board = yaml.safe_load((orc / "work" / "board.yaml").read_text())
        assert board["counter"] == 1
        assert board["tasks"][0]["name"] == filename
        assert board["tasks"][0]["status"] == "planned"

    def test_create_task_increments_counter(self, tmp_path):
        orc = _orc_dir(tmp_path)
        s = _state(orc)
        from orc.coordination.models import TaskBody

        _body = TaskBody(overview="x", in_scope=[], out_of_scope=[], steps=[])
        f1, _ = s.create_task("first", "0001-feat.md", _body)
        f2, _ = s.create_task("second", "0001-feat.md", _body)
        assert f1.startswith("0000-")
        assert f2.startswith("0001-")

    def test_set_task_status(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "work" / "board.yaml").write_text(
            "tasks:\n  - name: 0001-foo.md\n    status: planned\n"
        )
        _state(orc).set_task_status("0001-foo.md", "in-review")
        board = yaml.safe_load((orc / "work" / "board.yaml").read_text())
        assert board["tasks"][0]["status"] == "in-review"

    def test_assign_task_sets_assigned_to(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "work" / "board.yaml").write_text(
            "tasks:\n  - name: 0001-foo.md\n    status: planned\n"
        )
        _state(orc).assign_task("0001-foo.md", "coder-1")
        board = yaml.safe_load((orc / "work" / "board.yaml").read_text())
        assert board["tasks"][0]["assigned_to"] == "coder-1"
        assert board["tasks"][0]["status"] == "in-progress"

    def test_assign_task_preserves_advanced_status(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "work" / "board.yaml").write_text(
            "tasks:\n  - name: 0001-foo.md\n    status: in-review\n"
        )
        _state(orc).assign_task("0001-foo.md", "qa-1")
        board = yaml.safe_load((orc / "work" / "board.yaml").read_text())
        assert board["tasks"][0]["status"] == "in-review"

    def test_assign_task_not_found_warns(self, tmp_path):
        orc = _orc_dir(tmp_path)
        _state(orc).assign_task("9999-missing.md", "coder-1")  # should not raise

    def test_unassign_task(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "work" / "board.yaml").write_text(
            "tasks:\n  - name: 0001-foo.md\n    assigned_to: coder-1\n"
        )
        _state(orc).unassign_task("0001-foo.md")
        board = yaml.safe_load((orc / "work" / "board.yaml").read_text())
        assert board["tasks"][0].get("assigned_to") is None

    def test_unassign_task_not_found_no_write(self, tmp_path):
        orc = _orc_dir(tmp_path)
        _state(orc).unassign_task("9999-missing.md")  # should not raise

    def test_clear_all_assignments(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "work" / "board.yaml").write_text(
            "tasks:\n  - name: 0001-foo.md\n    assigned_to: coder-1\n"
        )
        _state(orc).clear_all_assignments()
        board = yaml.safe_load((orc / "work" / "board.yaml").read_text())
        assert board["tasks"][0].get("assigned_to") is None

    def test_clear_all_assignments_no_change_skips_write(self, tmp_path):
        # No assigned_to fields → no write needed (should not raise).
        orc = _orc_dir(tmp_path)
        _state(orc).clear_all_assignments()

    def test_add_task_comment(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "work" / "board.yaml").write_text(
            "tasks:\n  - name: 0001-foo.md\n    status: in-review\n"
        )
        _state(orc).add_task_comment("0001-foo.md", "qa-1", "Missing tests")
        board = yaml.safe_load((orc / "work" / "board.yaml").read_text())
        comments = board["tasks"][0]["comments"]
        assert len(comments) == 1
        assert comments[0]["from"] == "qa-1"
        assert comments[0]["text"] == "Missing tests"


class TestStateManagerVisions:
    def test_get_pending_visions_empty(self, tmp_path):
        orc = _orc_dir(tmp_path)
        assert _state(orc).get_pending_visions() == []

    def test_get_pending_visions_no_vision_dir(self, tmp_path):
        orc = tmp_path / ".orc"
        orc.mkdir(exist_ok=True)
        (orc / "work").mkdir(exist_ok=True)
        (orc / "work" / "board.yaml").write_text("tasks: []\n")
        from orc.coordination.state import BoardStateManager

        assert BoardStateManager(orc).get_pending_visions() == []

    def test_get_pending_visions_with_file(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "vision" / "ready" / "0001-feature.md").write_text("# Vision")
        visions = _state(orc).get_pending_visions()
        assert visions == ["0001-feature.md"]

    def test_get_pending_visions_skips_dotfiles(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "vision" / "ready" / ".future-work.md").write_text("# Future")
        assert _state(orc).get_pending_visions() == []

    def test_get_pending_visions_skips_readme(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "vision" / "ready" / "README.md").write_text("# README")
        assert _state(orc).get_pending_visions() == []

    def test_get_pending_visions_skips_matched_tasks(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "vision" / "ready" / "0001-feature.md").write_text("# Vision")
        (orc / "work" / "board.yaml").write_text(
            "tasks:\n  - name: 0001-feature.md\n    status: planned\n"
        )
        assert _state(orc).get_pending_visions() == []

    def test_get_pending_visions_skips_stem_matched_tasks(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "vision" / "ready" / "0001-feature.md").write_text("# Vision")
        (orc / "work" / "board.yaml").write_text("tasks:\n  - name: 0001-feature.md\n")
        assert _state(orc).get_pending_visions() == []

    def test_read_vision_found(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "vision" / "ready" / "0001-feature.md").write_text("# Feature Vision")
        content = _state(orc).read_vision("0001-feature.md")
        assert "Feature Vision" in content

    def test_read_vision_not_found(self, tmp_path):
        orc = _orc_dir(tmp_path)
        with pytest.raises(FileNotFoundError, match="Vision not found"):
            _state(orc).read_vision("9999-missing.md")

    def test_close_vision_moves_file_to_old_dir(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "vision" / "ready" / "0001-feature.md").write_text("# Feature")
        _state(orc).close_vision("0001-feature.md", "Built the feature.", ["0001-task.md"])
        assert not (orc / "vision" / "ready" / "0001-feature.md").exists()
        assert (orc / "vision" / "done" / "0001-feature.md").exists()

    def test_close_vision_does_not_write_changelog(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "vision" / "ready" / "0001-feature.md").write_text("# Feature")
        _state(orc).close_vision("0001-feature.md", "Summary.", [])
        assert not (orc / "orc-CHANGELOG.md").exists()

    def test_close_vision_does_not_modify_existing_changelog(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "vision" / "ready" / "0001-feature.md").write_text("# Feature")
        original = "# Existing changelog\n"
        (orc / "orc-CHANGELOG.md").write_text(original)
        _state(orc).close_vision("0001-feature.md", "Done.", [])
        assert (orc / "orc-CHANGELOG.md").read_text() == original

    def test_close_vision_accepts_empty_task_files(self, tmp_path):
        orc = _orc_dir(tmp_path)
        (orc / "vision" / "ready" / "0001-feature.md").write_text("# Feature")
        _state(orc).close_vision("0001-feature.md", "Summary.", [])
        assert (orc / "vision" / "done" / "0001-feature.md").exists()

    def test_close_vision_not_found_raises(self, tmp_path):
        orc = _orc_dir(tmp_path)
        with pytest.raises(FileNotFoundError, match="Vision not found"):
            _state(orc).close_vision("9999-missing.md", "nope", [])


# ─────────────────────────────────────────────────────────────────────────────
# Route handler tests (direct function calls, no HTTP transport)
# ─────────────────────────────────────────────────────────────────────────────


class TestBoardRoutes:
    """Test route handlers from orc.coordination.routes.board directly."""

    def _req(self, tmp_path):
        """Return a mock Request whose app.state.coord_state is a real BoardStateManager."""
        from orc.coordination.state import BoardStateManager

        req = MagicMock()
        orc = _orc_dir(tmp_path)
        req.app.state.coord_state = BoardStateManager(orc)
        return req

    def test_get_tasks_empty(self, tmp_path):
        from orc.coordination.routes.board import _get_state, get_tasks

        req = self._req(tmp_path)
        result = get_tasks(state=_get_state(req), status_filter=None)
        assert result == []

    def test_get_tasks_no_param_returns_all(self, tmp_path):
        from orc.coordination.routes.board import _get_state, get_tasks

        req = self._req(tmp_path)
        orc = tmp_path / ".orc"
        (orc / "work" / "board.yaml").write_text(
            "tasks:\n"
            "  - name: 0001-open.md\n    status: planned\n"
            "  - name: 0002-done.md\n    status: done\n"
        )
        result = get_tasks(state=_get_state(req), status_filter=None)
        names = [t.name for t in result]
        assert "0001-open.md" in names
        assert "0002-done.md" in names

    def test_get_tasks_status_filter_planned(self, tmp_path):
        from orc.coordination.routes.board import _get_state, get_tasks

        req = self._req(tmp_path)
        orc = tmp_path / ".orc"
        (orc / "work" / "board.yaml").write_text(
            "tasks:\n"
            "  - name: 0001-open.md\n    status: planned\n"
            "  - name: 0002-done.md\n    status: done\n"
        )
        result = get_tasks(state=_get_state(req), status_filter="planned")
        names = [t.name for t in result]
        assert names == ["0001-open.md"]

    def test_get_tasks_status_filter_done(self, tmp_path):
        from orc.coordination.routes.board import _get_state, get_tasks

        req = self._req(tmp_path)
        orc = tmp_path / ".orc"
        (orc / "work" / "board.yaml").write_text(
            "tasks:\n"
            "  - name: 0001-open.md\n    status: planned\n"
            "  - name: 0002-done.md\n    status: done\n"
        )
        result = get_tasks(state=_get_state(req), status_filter="done")
        names = [t.name for t in result]
        assert names == ["0002-done.md"]

    def test_get_tasks_status_all_returns_all(self, tmp_path):
        from orc.coordination.routes.board import _get_state, get_tasks

        req = self._req(tmp_path)
        orc = tmp_path / ".orc"
        (orc / "work" / "board.yaml").write_text(
            "tasks:\n"
            "  - name: 0001-open.md\n    status: planned\n"
            "  - name: 0002-done.md\n    status: done\n"
        )
        result = get_tasks(state=_get_state(req), status_filter=None)
        names = [t.name for t in result]
        assert "0001-open.md" in names
        assert "0002-done.md" in names
        assert len(result) == 2

    def test_create_task_returns_filename_and_path(self, tmp_path):
        from orc.coordination.models import CreateTaskRequest, TaskBody
        from orc.coordination.routes.board import _get_state, create_task

        req = self._req(tmp_path)
        body = CreateTaskRequest(
            title="add-auth",
            vision="0001-feat.md",
            body=TaskBody(overview="x", in_scope=[], out_of_scope=[], steps=[]),
        )
        result = create_task(body=body, state=_get_state(req))
        assert result.filename.endswith(".md")
        assert "add-auth" in result.filename

    def _make_task_req(self, title: str):
        from orc.coordination.models import CreateTaskRequest, TaskBody

        return CreateTaskRequest(
            title=title,
            vision="0001-feat.md",
            body=TaskBody(overview="x", in_scope=[], out_of_scope=[], steps=[]),
        )

    def test_get_task_found(self, tmp_path):
        from orc.coordination.routes.board import _get_state, create_task, get_task

        req = self._req(tmp_path)
        created = create_task(body=self._make_task_req("my-task"), state=_get_state(req))
        result = get_task(task_name=created.filename, state=_get_state(req))
        assert result.name == created.filename

    def test_get_task_not_found_raises_404(self, tmp_path):
        from fastapi import HTTPException

        from orc.coordination.routes.board import _get_state, get_task

        req = self._req(tmp_path)
        with pytest.raises(HTTPException) as exc_info:
            get_task(task_name="9999-missing.md", state=_get_state(req))
        assert exc_info.value.status_code == 404

    def test_set_status(self, tmp_path):
        from orc.coordination.models import SetStatusRequest
        from orc.coordination.routes.board import _get_state, create_task, get_task, set_status

        req = self._req(tmp_path)
        created = create_task(body=self._make_task_req("my-task"), state=_get_state(req))
        name = created.filename
        set_status(task_name=name, body=SetStatusRequest(status="in-review"), state=_get_state(req))
        task = get_task(task_name=name, state=_get_state(req))
        assert task.status == "in-review"

    def test_add_comment(self, tmp_path):
        from orc.coordination.models import AddCommentRequest
        from orc.coordination.routes.board import _get_state, add_comment, create_task, get_task

        req = self._req(tmp_path)
        created = create_task(body=self._make_task_req("my-task"), state=_get_state(req))
        name = created.filename
        result = add_comment(
            task_name=name,
            body=AddCommentRequest(author="qa-1", text="See line 42"),
            state=_get_state(req),
        )
        assert result.ok is True
        task = get_task(task_name=name, state=_get_state(req))
        assert task.comments[0].text == "See line 42"

    def test_get_task_content_found(self, tmp_path):
        from orc.coordination.routes.board import _get_state, create_task, get_task_content

        req = self._req(tmp_path)
        created = create_task(body=self._make_task_req("my-task"), state=_get_state(req))
        name = created.filename
        result = get_task_content(task_name=name, state=_get_state(req))
        assert result.name == name
        assert isinstance(result.content, str)

    def test_get_task_content_not_found_raises_404(self, tmp_path):
        from fastapi import HTTPException

        from orc.coordination.routes.board import _get_state, get_task_content

        req = self._req(tmp_path)
        with pytest.raises(HTTPException) as exc_info:
            get_task_content(task_name="9999-missing.md", state=_get_state(req))
        assert exc_info.value.status_code == 404


class TestVisionRoutes:
    """Test route handlers from orc.coordination.routes.visions directly."""

    def _req(self, tmp_path):
        from orc.coordination.state import BoardStateManager

        req = MagicMock()
        orc = _orc_dir(tmp_path)
        req.app.state.coord_state = BoardStateManager(orc)
        return req

    def test_get_pending_visions_empty(self, tmp_path):
        from orc.coordination.routes.visions import _get_state, get_pending_visions

        req = self._req(tmp_path)
        assert get_pending_visions(state=_get_state(req)) == []

    def test_get_pending_visions_with_file(self, tmp_path):
        from orc.coordination.routes.visions import _get_state, get_pending_visions

        orc = _orc_dir(tmp_path)
        (orc / "vision" / "ready" / "0001-feat.md").write_text("# Vision")
        req = self._req(tmp_path)
        result = get_pending_visions(state=_get_state(req))
        assert "0001-feat.md" in result

    def test_get_vision_content(self, tmp_path):
        from orc.coordination.routes.visions import _get_state, get_vision

        orc = _orc_dir(tmp_path)
        (orc / "vision" / "ready" / "0001-feat.md").write_text("# Vision content")
        req = self._req(tmp_path)
        result = get_vision(name="0001-feat.md", state=_get_state(req))
        assert result.content == "# Vision content"
        assert result.name == "0001-feat.md"

    def test_get_vision_not_found_raises_404(self, tmp_path):
        from fastapi import HTTPException

        from orc.coordination.routes.visions import _get_state, get_vision

        req = self._req(tmp_path)
        with pytest.raises(HTTPException) as exc_info:
            get_vision(name="9999-missing.md", state=_get_state(req))
        assert exc_info.value.status_code == 404

    def test_close_vision(self, tmp_path):
        from orc.coordination.models import CloseVisionRequest
        from orc.coordination.routes.visions import _get_state, close_vision

        orc = _orc_dir(tmp_path)
        (orc / "vision" / "ready" / "0001-feat.md").write_text("# Vision")
        req = self._req(tmp_path)
        close_vision(
            name="0001-feat.md",
            body=CloseVisionRequest(summary="Done.", task_files=["0001-task.md"]),
            state=_get_state(req),
        )
        assert not (orc / "vision" / "ready" / "0001-feat.md").exists()

    def test_close_vision_not_found_raises_404(self, tmp_path):
        from fastapi import HTTPException

        from orc.coordination.models import CloseVisionRequest
        from orc.coordination.routes.visions import _get_state, close_vision

        req = self._req(tmp_path)
        with pytest.raises(HTTPException) as exc_info:
            close_vision(
                name="9999-missing.md",
                body=CloseVisionRequest(summary="nope", task_files=[]),
                state=_get_state(req),
            )
        assert exc_info.value.status_code == 404


class TestWorkRoutes:
    """Test the health/work route."""

    def _req(self, tmp_path):
        from orc.coordination.state import BoardStateManager

        req = MagicMock()
        orc = _orc_dir(tmp_path)
        req.app.state.coord_state = BoardStateManager(orc)
        return req

    def test_health_returns_ok(self, tmp_path):
        from orc.coordination.routes.work import _get_state, health

        req = self._req(tmp_path)
        result = health(state=_get_state(req))
        assert result.status == "ok"
        assert isinstance(result.pid, int)


# ─────────────────────────────────────────────────────────────────────────────
# App factory test
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateApp:
    def test_app_has_board_route(self, tmp_path):
        from orc.coordination.app import create_app
        from orc.coordination.state import BoardStateManager

        orc = _orc_dir(tmp_path)
        app = create_app(BoardStateManager(orc))
        routes = {r.path for r in app.routes}  # type: ignore[attr-defined]
        assert "/board/tasks" in routes

    def test_app_has_visions_route(self, tmp_path):
        from orc.coordination.app import create_app
        from orc.coordination.state import BoardStateManager

        orc = _orc_dir(tmp_path)
        app = create_app(BoardStateManager(orc))
        routes = {r.path for r in app.routes}  # type: ignore[attr-defined]
        assert "/visions" in routes

    def test_app_has_health_route(self, tmp_path):
        from orc.coordination.app import create_app
        from orc.coordination.state import BoardStateManager

        orc = _orc_dir(tmp_path)
        app = create_app(BoardStateManager(orc))
        routes = {r.path for r in app.routes}  # type: ignore[attr-defined]
        assert "/health" in routes

    def test_app_state_has_coord_state(self, tmp_path):
        from orc.coordination.app import create_app
        from orc.coordination.state import BoardStateManager

        orc = _orc_dir(tmp_path)
        state = BoardStateManager(orc)
        app = create_app(state)
        assert app.state.coord_state is state


# ─────────────────────────────────────────────────────────────────────────────
# CoordinationServer lifecycle tests
# ─────────────────────────────────────────────────────────────────────────────


class TestCoordinationServer:
    def test_stop_without_start_is_safe(self, tmp_path):
        """stop() when never started must not raise."""
        from orc.coordination.server import CoordinationServer
        from orc.coordination.state import BoardStateManager

        orc = _orc_dir(tmp_path)
        server = CoordinationServer(BoardStateManager(orc), tmp_path / "orc.sock")
        server.stop()  # should not raise

    def test_start_creates_socket_and_stop_removes_it(self, tmp_path):
        """Real server: socket appears on start, disappears on stop."""
        from orc.coordination.server import CoordinationServer
        from orc.coordination.state import BoardStateManager

        orc = _orc_dir(tmp_path)
        sock = tmp_path / "orc.sock"
        server = CoordinationServer(BoardStateManager(orc), sock)
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
        from orc.coordination.state import BoardStateManager

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
        server = CoordinationServer(BoardStateManager(orc), sock)

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
        from orc.coordination.state import BoardStateManager

        orc = _orc_dir(tmp_path)
        sock = tmp_path / "orc.sock"
        sock.write_text("stale")  # simulate a stale socket
        server = CoordinationServer(BoardStateManager(orc), sock)
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
        from orc.coordination.models import CreateTaskRequest, TaskBody

        m = CreateTaskRequest(
            title="foo-bar",
            vision="0001-feat.md",
            body=TaskBody(overview="x", in_scope=[], out_of_scope=[], steps=[]),
        )
        assert m.title == "foo-bar"

    def test_create_task_response(self):
        from orc.coordination.models import CreateTaskResponse

        m = CreateTaskResponse(filename="0001-foo.md", path="/tmp/0001-foo.md")
        assert m.filename == "0001-foo.md"

    def test_set_status_request(self):
        from orc.coordination.models import SetStatusRequest

        m = SetStatusRequest(status="in-review")
        assert m.status == "in-review"

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
        assert m.commit_tag is None
        assert m.timestamp is None

    def test_task_entry_done_fields(self):
        from orc.coordination.models import TaskEntry

        m = TaskEntry(name="0001-foo.md", commit_tag="abc123", timestamp="2026-01-01T00:00:00Z")
        assert m.commit_tag == "abc123"
        assert m.timestamp == "2026-01-01T00:00:00Z"

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
        from orc.coordination.board import FileBoardManager

        orc = tmp_path / ".orc"
        orc.mkdir(exist_ok=True)
        (orc / "work").mkdir(exist_ok=True)
        (orc / "work" / "board.yaml").write_text("counter: 0\ntasks: []\n")
        return FileBoardManager(orc)

    def test_create_task_returns_filename_and_path(self, tmp_path):
        mgr = self._mgr(tmp_path)
        from orc.coordination.models import TaskBody

        _body = TaskBody(overview="x", in_scope=[], out_of_scope=[], steps=[])
        filename, path = mgr.create_task("add-auth", "0001-feat.md", _body)
        assert filename == "0000-add-auth.md"
        assert path.exists()

    def test_create_task_increments_counter(self, tmp_path):
        mgr = self._mgr(tmp_path)
        from orc.coordination.models import TaskBody

        _body = TaskBody(overview="x", in_scope=[], out_of_scope=[], steps=[])
        mgr.create_task("first", "0001-feat.md", _body)
        f2, _ = mgr.create_task("second", "0001-feat.md", _body)
        assert f2.startswith("0001-")

    def test_create_task_adds_planned_entry_to_board(self, tmp_path):
        orc = tmp_path / ".orc"
        mgr = self._mgr(tmp_path)
        from orc.coordination.models import TaskBody

        _body = TaskBody(overview="x", in_scope=[], out_of_scope=[], steps=[])
        mgr.create_task("my-task", "0001-feat.md", _body)
        board = yaml.safe_load((orc / "work" / "board.yaml").read_text())
        assert board["tasks"][0]["status"] == "planned"


# ─────────────────────────────────────────────────────────────────────────────
# Config: api_socket_path
# ─────────────────────────────────────────────────────────────────────────────


class TestConfigApiSocketPath:
    def test_api_socket_path_is_set(self, tmp_path, _init_config):
        import orc.config as _cfg

        cfg = _cfg.get()
        assert cfg.api_socket_path == cfg.orc_dir / "run" / "orc.sock"


# ─────────────────────────────────────────────────────────────────────────────
# BoardSnapshot client tests
# ─────────────────────────────────────────────────────────────────────────────


class TestGetBoardSnapshot:
    """Tests for orc.coordination.client.get_board_snapshot().

    We mock at the ``orc.coordination.client`` module level (not the ``httpx``
    top-level module) because conftest.py stubs out ``httpx`` globally.
    """

    def test_returns_none_when_socket_env_not_set(self, monkeypatch):
        from orc.coordination.client import get_board_snapshot

        monkeypatch.delenv("ORC_API_SOCKET", raising=False)
        assert get_board_snapshot() is None

    def test_returns_none_when_socket_empty_string(self, monkeypatch):
        from orc.coordination.client import get_board_snapshot

        monkeypatch.setenv("ORC_API_SOCKET", "")
        assert get_board_snapshot() is None

    def test_returns_none_when_connection_fails(self, monkeypatch):
        import orc.coordination.client as _client_mod
        from orc.coordination.client import get_board_snapshot

        monkeypatch.setenv("ORC_API_SOCKET", "/tmp/nonexistent-orc-test.sock")

        class _RaisingClient:
            def __enter__(self):
                raise Exception("connection refused")

            def __exit__(self, *a):
                pass

        _mk = _client_mod.httpx
        monkeypatch.setattr(_mk, "Client", lambda transport, base_url: _RaisingClient())
        monkeypatch.setattr(_mk, "HTTPTransport", lambda uds: None, raising=False)

        result = get_board_snapshot()
        assert result is None

    def test_returns_board_snapshot_on_success(self, monkeypatch):
        import orc.coordination.client as _client_mod
        from orc.coordination.client import BoardSnapshot, get_board_snapshot

        monkeypatch.setenv("ORC_API_SOCKET", "/tmp/fake.sock")

        class _FakeResponse:
            def __init__(self, data):
                self._data = data

            def json(self):
                return self._data

        class _FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get(self, path):
                if path == "/visions":
                    return _FakeResponse(["0007-vision.md"])
                if path == "/board/tasks":
                    return _FakeResponse([{"name": "0001-task.md", "status": "planned"}])
                raise ValueError(f"Unknown path: {path}")

        monkeypatch.setattr(_client_mod.httpx, "Client", lambda transport, base_url: _FakeClient())
        monkeypatch.setattr(_client_mod.httpx, "HTTPTransport", lambda uds: None, raising=False)

        result = get_board_snapshot()
        assert isinstance(result, BoardSnapshot)
        assert result.visions == ["0007-vision.md"]
        assert result.tasks[0].name == "0001-task.md"

    def test_returns_none_when_json_parse_fails(self, monkeypatch):
        import orc.coordination.client as _client_mod
        from orc.coordination.client import get_board_snapshot

        monkeypatch.setenv("ORC_API_SOCKET", "/tmp/fake.sock")

        class _BadResponse:
            def json(self):
                raise ValueError("bad json")

        class _FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def get(self, path):
                return _BadResponse()

        monkeypatch.setattr(_client_mod.httpx, "Client", lambda transport, base_url: _FakeClient())
        monkeypatch.setattr(_client_mod.httpx, "HTTPTransport", lambda uds: None, raising=False)

        result = get_board_snapshot()
        assert result is None
