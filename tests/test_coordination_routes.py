"""Tests for orc.coordination route handlers (board, visions, work)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── helpers ──────────────────────────────────────────────────────────────────


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


def _make_create_request(title: str):
    """Return a minimal valid CreateTaskRequest for testing."""
    from orc.coordination.models import CreateTaskRequest, TaskBody

    return CreateTaskRequest(
        title=title,
        vision="0001-test-vision.md",
        body=TaskBody(
            overview="Implement the feature.",
            in_scope=["core logic"],
            out_of_scope=["UI changes"],
            steps=["Write tests", "Implement"],
            notes="",
        ),
    )


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
        result = get_tasks(state=_get_state(req))
        assert result == []

    def test_create_task_returns_filename_and_path(self, tmp_path):
        from orc.coordination.routes.board import _get_state, create_task

        req = self._req(tmp_path)
        body = _make_create_request("add-auth")
        result = create_task(body=body, state=_get_state(req))
        assert result["filename"].endswith(".md")
        assert "add-auth" in result["filename"]

    def test_get_task_found(self, tmp_path):
        from orc.coordination.routes.board import _get_state, create_task, get_task

        req = self._req(tmp_path)
        created = create_task(body=_make_create_request("my-task"), state=_get_state(req))
        result = get_task(task_name=created["filename"], state=_get_state(req))
        assert result["name"] == created["filename"]

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
        created = create_task(body=_make_create_request("my-task"), state=_get_state(req))
        name = created["filename"]
        set_status(task_name=name, body=SetStatusRequest(status="in-review"), state=_get_state(req))
        task = get_task(task_name=name, state=_get_state(req))
        assert task["status"] == "in-review"

    def test_add_comment(self, tmp_path):
        from orc.coordination.models import AddCommentRequest
        from orc.coordination.routes.board import _get_state, add_comment, create_task, get_task

        req = self._req(tmp_path)
        created = create_task(body=_make_create_request("my-task"), state=_get_state(req))
        name = created["filename"]
        result = add_comment(
            task_name=name,
            body=AddCommentRequest(author="qa-1", text="See line 42"),
            state=_get_state(req),
        )
        assert result == {"ok": True}
        task = get_task(task_name=name, state=_get_state(req))
        assert task["comments"][0]["text"] == "See line 42"


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
        assert result["content"] == "# Vision content"
        assert result["name"] == "0001-feat.md"

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
        assert result["status"] == "ok"
        assert isinstance(result["pid"], int)


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
