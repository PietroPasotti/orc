"""Tests for the orc MCP server — tool implementations and role filtering."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import orc.mcp.tools as _tools
from orc.mcp.client import find_task_by_code
from orc.mcp.server import _build_server, _get_role

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_httpx_resp(status_code: int = 200, json_body: object = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------


class TestGetRole:
    def test_returns_planner(self, monkeypatch):
        monkeypatch.setenv("ORC_AGENT_ROLE", "planner")
        assert _get_role() == "planner"

    def test_returns_coder(self, monkeypatch):
        monkeypatch.setenv("ORC_AGENT_ROLE", "coder")
        assert _get_role() == "coder"

    def test_returns_qa(self, monkeypatch):
        monkeypatch.setenv("ORC_AGENT_ROLE", "qa")
        assert _get_role() == "qa"

    def test_unknown_role_falls_back_to_coder(self, monkeypatch):
        monkeypatch.setenv("ORC_AGENT_ROLE", "wizard")
        assert _get_role() == "coder"

    def test_empty_role_falls_back_to_coder(self, monkeypatch):
        monkeypatch.delenv("ORC_AGENT_ROLE", raising=False)
        assert _get_role() == "coder"


class TestBuildServer:
    def _tool_names(self, role: str, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        monkeypatch.setenv("ORC_AGENT_ROLE", role)
        server = _build_server()
        return [t.name for t in server._tool_manager.list_tools()]

    def test_shared_tools_always_registered(self, monkeypatch):
        for role in ("planner", "coder", "qa"):
            names = self._tool_names(role, monkeypatch)
            assert "get_task" in names, f"get_task missing for {role}"
            assert "update_task_status" in names, f"update_task_status missing for {role}"
            assert "add_comment" in names, f"add_comment missing for {role}"

    def test_planner_gets_planner_tools(self, monkeypatch):
        names = self._tool_names("planner", monkeypatch)
        assert "get_vision" in names
        assert "create_task" in names
        assert "close_vision" in names

    def test_planner_does_not_get_coder_or_qa_tools(self, monkeypatch):
        names = self._tool_names("planner", monkeypatch)
        assert "close_task" not in names
        assert "review_task" not in names

    def test_coder_gets_close_task(self, monkeypatch):
        names = self._tool_names("coder", monkeypatch)
        assert "close_task" in names

    def test_coder_does_not_get_planner_or_qa_tools(self, monkeypatch):
        names = self._tool_names("coder", monkeypatch)
        assert "get_vision" not in names
        assert "create_task" not in names
        assert "review_task" not in names

    def test_qa_gets_review_task(self, monkeypatch):
        names = self._tool_names("qa", monkeypatch)
        assert "review_task" in names

    def test_qa_does_not_get_planner_or_coder_tools(self, monkeypatch):
        names = self._tool_names("qa", monkeypatch)
        assert "get_vision" not in names
        assert "close_task" not in names

    def test_merger_gets_close_merge(self, monkeypatch):
        names = self._tool_names("merger", monkeypatch)
        assert "close_merge" in names

    def test_merger_does_not_get_close_task(self, monkeypatch):
        names = self._tool_names("merger", monkeypatch)
        assert "close_task" not in names
        assert "review_task" not in names


# ---------------------------------------------------------------------------
# Client helpers
# ---------------------------------------------------------------------------


class TestFindTaskByCode:
    def test_finds_matching_task(self):
        client = MagicMock()
        client.get.return_value = _make_httpx_resp(
            200, [{"name": "0002-add-auth.md"}, {"name": "0003-fix-bug.md"}]
        )
        assert find_task_by_code(client, "0002") == "0002-add-auth.md"

    def test_raises_when_not_found(self):
        client = MagicMock()
        client.get.return_value = _make_httpx_resp(200, [{"name": "0003-fix-bug.md"}])
        with pytest.raises(ValueError, match="0002"):
            find_task_by_code(client, "0002")


# ---------------------------------------------------------------------------
# Shared tools
# ---------------------------------------------------------------------------


class TestGetTask:
    def test_returns_content_and_conversation(self, monkeypatch):
        monkeypatch.setenv("ORC_API_SOCKET", "/fake.sock")

        def fake_get(url: str) -> MagicMock:
            if "/content" in url:
                return _make_httpx_resp(200, {"content": "# Task\n\nDo stuff."})
            return _make_httpx_resp(
                200,
                {
                    "name": "0001-foo.md",
                    "comments": [{"from": "coder-1", "ts": "2025-01-01T00:00:00Z", "text": "done"}],
                },
            )

        client_mock = MagicMock()
        client_mock.__enter__ = MagicMock(return_value=client_mock)
        client_mock.__exit__ = MagicMock(return_value=False)
        client_mock.get.side_effect = fake_get

        with patch("orc.mcp.tools.get_client") as mock_gc:
            mock_gc.return_value = client_mock
            result = _tools.get_task("0001-foo.md")

        assert "# Task" in result
        assert "## Conversation" in result
        assert "coder-1" in result
        assert "done" in result

    def test_raises_on_404(self, monkeypatch):
        monkeypatch.setenv("ORC_API_SOCKET", "/fake.sock")
        client_mock = MagicMock()
        client_mock.__enter__ = MagicMock(return_value=client_mock)
        client_mock.__exit__ = MagicMock(return_value=False)
        client_mock.get.return_value = _make_httpx_resp(404)

        with patch("orc.mcp.tools.get_client") as mock_gc:
            mock_gc.return_value = client_mock
            with pytest.raises(ValueError, match="not found"):
                _tools.get_task("0001-missing.md")

    def test_no_comments_shows_placeholder(self, monkeypatch):
        monkeypatch.setenv("ORC_API_SOCKET", "/fake.sock")

        def fake_get(url: str) -> MagicMock:
            if "/content" in url:
                return _make_httpx_resp(200, {"content": "# Task"})
            return _make_httpx_resp(200, {"name": "0001-foo.md", "comments": []})

        client_mock = MagicMock()
        client_mock.__enter__ = MagicMock(return_value=client_mock)
        client_mock.__exit__ = MagicMock(return_value=False)
        client_mock.get.side_effect = fake_get

        with patch("orc.mcp.tools.get_client") as mock_gc:
            mock_gc.return_value = client_mock
            result = _tools.get_task("0001-foo.md")

        assert "No comments yet" in result


class TestUpdateTaskStatus:
    def test_updates_status(self, monkeypatch):
        monkeypatch.setenv("ORC_API_SOCKET", "/fake.sock")
        client_mock = MagicMock()
        client_mock.__enter__ = MagicMock(return_value=client_mock)
        client_mock.__exit__ = MagicMock(return_value=False)
        client_mock.get.return_value = _make_httpx_resp(200, [{"name": "0002-add-auth.md"}])
        client_mock.put.return_value = _make_httpx_resp(204)

        with patch("orc.mcp.tools.get_client") as mock_gc:
            mock_gc.return_value = client_mock
            result = _tools.update_task_status("0002", "in-progress")

        assert "0002-add-auth.md" in result
        assert "in-progress" in result

    def test_invalid_status_raises(self):
        with pytest.raises(ValueError, match="Invalid status"):
            _tools.update_task_status("0002", "turbo")


class TestAddComment:
    def test_adds_comment(self, monkeypatch):
        monkeypatch.setenv("ORC_API_SOCKET", "/fake.sock")
        monkeypatch.setenv("ORC_AGENT_ID", "coder-1")
        client_mock = MagicMock()
        client_mock.__enter__ = MagicMock(return_value=client_mock)
        client_mock.__exit__ = MagicMock(return_value=False)
        client_mock.get.return_value = _make_httpx_resp(200, [{"name": "0003-fix-bug.md"}])
        client_mock.post.return_value = _make_httpx_resp(201)

        with patch("orc.mcp.tools.get_client") as mock_gc:
            mock_gc.return_value = client_mock
            result = _tools.add_comment("0003", "blocked on missing spec")

        assert "0003-fix-bug.md" in result
        call_args = client_mock.post.call_args
        assert call_args[1]["json"]["author"] == "coder-1"
        assert call_args[1]["json"]["text"] == "blocked on missing spec"


# ---------------------------------------------------------------------------
# Planner tools
# ---------------------------------------------------------------------------


class TestGetVision:
    def test_returns_vision_content(self, monkeypatch):
        monkeypatch.setenv("ORC_API_SOCKET", "/fake.sock")
        client_mock = MagicMock()
        client_mock.__enter__ = MagicMock(return_value=client_mock)
        client_mock.__exit__ = MagicMock(return_value=False)
        client_mock.get.return_value = _make_httpx_resp(
            200, {"content": "# Vision\n\nBuild something."}
        )

        with patch("orc.mcp.tools.get_client") as mock_gc:
            mock_gc.return_value = client_mock
            result = _tools.get_vision("0001-shark-fleet.md")

        assert "Build something" in result

    def test_raises_on_404(self, monkeypatch):
        monkeypatch.setenv("ORC_API_SOCKET", "/fake.sock")
        client_mock = MagicMock()
        client_mock.__enter__ = MagicMock(return_value=client_mock)
        client_mock.__exit__ = MagicMock(return_value=False)
        client_mock.get.return_value = _make_httpx_resp(404)

        with patch("orc.mcp.tools.get_client") as mock_gc:
            mock_gc.return_value = client_mock
            with pytest.raises(ValueError, match="not found"):
                _tools.get_vision("0099-missing.md")


class TestCreateTask:
    def test_creates_task_and_commits(self, monkeypatch):
        monkeypatch.setenv("ORC_API_SOCKET", "/fake.sock")
        monkeypatch.setenv("ORC_AGENT_ID", "planner-1")
        client_mock = MagicMock()
        client_mock.__enter__ = MagicMock(return_value=client_mock)
        client_mock.__exit__ = MagicMock(return_value=False)
        client_mock.post.return_value = _make_httpx_resp(
            201, {"filename": "0004-add-auth.md", "path": "/fake/path"}
        )

        with (
            patch("orc.mcp.tools.get_client") as mock_gc,
            patch("orc.mcp.tools._run_git") as mock_git,
        ):
            mock_gc.return_value = client_mock
            result = _tools.create_task(
                task_title="add-auth",
                vision_file="0001-vision.md",
                overview="Implement auth",
                in_scope=["login"],
                out_of_scope=["2FA"],
                steps=["write tests", "implement"],
            )

        assert result == "0004-add-auth.md"
        mock_git.assert_called_once_with(
            "commit",
            "--allow-empty",
            "--no-verify",
            "-m",
            mock_git.call_args[0][4],
            cwd=None,
        )

    def test_stages_extra_files(self, monkeypatch):
        monkeypatch.setenv("ORC_API_SOCKET", "/fake.sock")
        monkeypatch.setenv("ORC_AGENT_ID", "planner-1")
        client_mock = MagicMock()
        client_mock.__enter__ = MagicMock(return_value=client_mock)
        client_mock.__exit__ = MagicMock(return_value=False)
        client_mock.post.return_value = _make_httpx_resp(
            201, {"filename": "0005-foo.md", "path": "/fake/path"}
        )

        git_calls = []
        with (
            patch("orc.mcp.tools.get_client") as mock_gc,
            patch("orc.mcp.tools._run_git", side_effect=lambda *a, **kw: git_calls.append(a)),
        ):
            mock_gc.return_value = client_mock
            _tools.create_task(
                task_title="foo",
                vision_file="0001-vision.md",
                overview="x",
                in_scope=[],
                out_of_scope=[],
                steps=[],
                extra_files=["docs/adr-0003.md"],
            )

        add_calls = [c for c in git_calls if c[0] == "add"]
        assert any("docs/adr-0003.md" in c for c in add_calls)


class TestCloseVision:
    def test_closes_vision(self, monkeypatch):
        monkeypatch.setenv("ORC_API_SOCKET", "/fake.sock")
        client_mock = MagicMock()
        client_mock.__enter__ = MagicMock(return_value=client_mock)
        client_mock.__exit__ = MagicMock(return_value=False)
        client_mock.post.return_value = _make_httpx_resp(200)

        with patch("orc.mcp.tools.get_client") as mock_gc:
            mock_gc.return_value = client_mock
            result = _tools.close_vision(
                "0001-shark-fleet.md", "Vision completed.", ["0004-task.md"]
            )

        assert "0001-shark-fleet.md" in result
        call_json = client_mock.post.call_args[1]["json"]
        assert call_json["summary"] == "Vision completed."
        assert "0004-task.md" in call_json["task_files"]


# ---------------------------------------------------------------------------
# Coder tools
# ---------------------------------------------------------------------------


class TestCloseTask:
    def test_stages_commits_and_sets_in_review(self, monkeypatch):
        monkeypatch.setenv("ORC_API_SOCKET", "/fake.sock")
        client_mock = MagicMock()
        client_mock.__enter__ = MagicMock(return_value=client_mock)
        client_mock.__exit__ = MagicMock(return_value=False)
        client_mock.get.return_value = _make_httpx_resp(200, [{"name": "0002-add-auth.md"}])
        client_mock.put.return_value = _make_httpx_resp(204)

        git_calls: list[tuple[str, ...]] = []
        with (
            patch("orc.mcp.tools.get_client") as mock_gc,
            patch("orc.mcp.tools._run_git", side_effect=lambda *a, **kw: git_calls.append(a)),
        ):
            mock_gc.return_value = client_mock
            result = _tools.close_task("0002", "auth module implemented")

        assert ("add", "-A") in git_calls
        commit_args = [c for c in git_calls if c[0] == "commit"]
        assert commit_args
        assert "feat(0002):" in commit_args[0][4]
        assert "in-review" in result

    def test_commit_message_includes_message(self, monkeypatch):
        monkeypatch.setenv("ORC_API_SOCKET", "/fake.sock")
        client_mock = MagicMock()
        client_mock.__enter__ = MagicMock(return_value=client_mock)
        client_mock.__exit__ = MagicMock(return_value=False)
        client_mock.get.return_value = _make_httpx_resp(200, [{"name": "0002-add-auth.md"}])
        client_mock.put.return_value = _make_httpx_resp(204)

        git_calls: list[tuple[str, ...]] = []
        with (
            patch("orc.mcp.tools.get_client") as mock_gc,
            patch("orc.mcp.tools._run_git", side_effect=lambda *a, **kw: git_calls.append(a)),
        ):
            mock_gc.return_value = client_mock
            _tools.close_task("0002", "custom message here")

        commit_call = next(c for c in git_calls if c[0] == "commit")
        assert "custom message here" in commit_call[4]


# ---------------------------------------------------------------------------
# Merger tools
# ---------------------------------------------------------------------------


class TestCloseMerge:
    def test_stages_commits_and_deletes_task(self, monkeypatch):
        monkeypatch.setenv("ORC_API_SOCKET", "/fake.sock")
        client_mock = MagicMock()
        client_mock.__enter__ = MagicMock(return_value=client_mock)
        client_mock.__exit__ = MagicMock(return_value=False)
        client_mock.get.return_value = _make_httpx_resp(200, [{"name": "0002-add-auth.md"}])
        client_mock.delete.return_value = _make_httpx_resp(204)

        git_calls: list[tuple[str, ...]] = []
        with (
            patch("orc.mcp.tools.get_client") as mock_gc,
            patch("orc.mcp.tools._run_git", side_effect=lambda *a, **kw: git_calls.append(a)),
        ):
            mock_gc.return_value = client_mock
            result = _tools.close_merge("0002", "Merged into dev")

        assert ("add", "-A") in git_calls
        commit_args = [c for c in git_calls if c[0] == "commit"]
        assert commit_args
        assert "feat(0002):" in commit_args[0][4]
        client_mock.delete.assert_called_once_with("/board/tasks/0002-add-auth.md")
        assert "removed from board" in result


class TestReviewTask:
    def test_approve(self, monkeypatch):
        monkeypatch.setenv("ORC_API_SOCKET", "/fake.sock")
        monkeypatch.setenv("ORC_AGENT_ID", "qa-1")
        client_mock = MagicMock()
        client_mock.__enter__ = MagicMock(return_value=client_mock)
        client_mock.__exit__ = MagicMock(return_value=False)
        client_mock.get.return_value = _make_httpx_resp(200, [{"name": "0002-add-auth.md"}])
        client_mock.put.return_value = _make_httpx_resp(204)

        with patch("orc.mcp.tools.get_client") as mock_gc, patch("orc.mcp.tools._run_git"):
            mock_gc.return_value = client_mock
            result = _tools.review_task("0002", "done", "all tests green")

        assert "approved" in result
        put_call = client_mock.put.call_args
        assert put_call[1]["json"]["status"] == "done"
        client_mock.post.assert_not_called()

    def test_reject_adds_comment(self, monkeypatch):
        monkeypatch.setenv("ORC_API_SOCKET", "/fake.sock")
        monkeypatch.setenv("ORC_AGENT_ID", "qa-1")
        client_mock = MagicMock()
        client_mock.__enter__ = MagicMock(return_value=client_mock)
        client_mock.__exit__ = MagicMock(return_value=False)
        client_mock.get.return_value = _make_httpx_resp(200, [{"name": "0002-add-auth.md"}])
        client_mock.put.return_value = _make_httpx_resp(204)
        client_mock.post.return_value = _make_httpx_resp(201)

        with patch("orc.mcp.tools.get_client") as mock_gc, patch("orc.mcp.tools._run_git"):
            mock_gc.return_value = client_mock
            result = _tools.review_task("0002", "in-progress", "missing error handling")

        assert "rejected" in result
        client_mock.post.assert_called_once()
        comment_json = client_mock.post.call_args[1]["json"]
        assert comment_json["text"] == "missing error handling"
        assert comment_json["author"] == "qa-1"

    def test_invalid_outcome_raises(self):
        with pytest.raises(ValueError, match="Invalid outcome"):
            _tools.review_task("0002", "meh", "whatever")

    def test_commit_message_includes_verdict(self, monkeypatch):
        monkeypatch.setenv("ORC_API_SOCKET", "/fake.sock")
        client_mock = MagicMock()
        client_mock.__enter__ = MagicMock(return_value=client_mock)
        client_mock.__exit__ = MagicMock(return_value=False)
        client_mock.get.return_value = _make_httpx_resp(200, [{"name": "0002-add-auth.md"}])
        client_mock.put.return_value = _make_httpx_resp(204)

        git_calls: list[tuple[str, ...]] = []
        with (
            patch("orc.mcp.tools.get_client") as mock_gc,
            patch("orc.mcp.tools._run_git", side_effect=lambda *a, **kw: git_calls.append(a)),
        ):
            mock_gc.return_value = client_mock
            _tools.review_task("0002", "done", "looks great")

        commit_call = next(c for c in git_calls if c[0] == "commit")
        assert "approved" in commit_call[5]


# ---------------------------------------------------------------------------
# Git helper
# ---------------------------------------------------------------------------


class TestRunGit:
    def test_success(self):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            _tools._run_git("status")  # should not raise

    def test_failure_raises(self):
        with patch(
            "subprocess.run",
            return_value=MagicMock(returncode=1, stderr="fatal: not a repo"),
        ):
            with pytest.raises(RuntimeError, match="git status failed"):
                _tools._run_git("status")


# ---------------------------------------------------------------------------
# MCP config generation — no longer applicable
# ---------------------------------------------------------------------------
# The internal backend calls tools in-process; MCP config generation was
# part of the CLI-based backends (CopilotBackend/ClaudeBackend) that have
# been removed.  The MCP server still exists for external tooling but the
# internal backend bypasses it.


# ---------------------------------------------------------------------------
# Client: _get_socket_path and get_client
# ---------------------------------------------------------------------------


class TestGetSocketPath:
    def test_raises_when_env_not_set(self, monkeypatch):
        from orc.mcp.client import _get_socket_path

        monkeypatch.delenv("ORC_API_SOCKET", raising=False)
        with pytest.raises(RuntimeError, match="ORC_API_SOCKET is not set"):
            _get_socket_path()

    def test_raises_when_socket_missing(self, monkeypatch, tmp_path):
        from orc.mcp.client import _get_socket_path

        monkeypatch.setenv("ORC_API_SOCKET", str(tmp_path / "missing.sock"))
        with pytest.raises(RuntimeError, match="does not exist"):
            _get_socket_path()

    def test_returns_path_when_socket_exists(self, monkeypatch, tmp_path):
        from orc.mcp.client import _get_socket_path

        sock = tmp_path / "orc.sock"
        sock.touch()
        monkeypatch.setenv("ORC_API_SOCKET", str(sock))
        assert _get_socket_path() == str(sock)


class TestGetClient:
    def test_yields_httpx_client(self, monkeypatch, tmp_path):
        from orc.mcp.client import get_client

        sock = tmp_path / "orc.sock"
        sock.touch()
        monkeypatch.setenv("ORC_API_SOCKET", str(sock))

        with patch("httpx.Client") as mock_client_cls:
            mock_instance = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_instance)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

            with get_client() as c:
                assert c is mock_instance


# ---------------------------------------------------------------------------
# Server: run() and __main__
# ---------------------------------------------------------------------------


class TestServerRun:
    def test_run_calls_server_run(self, monkeypatch):
        from orc.mcp.server import run

        monkeypatch.setenv("ORC_AGENT_ROLE", "coder")
        with patch("orc.mcp.server._build_server") as mock_build:
            mock_srv = MagicMock()
            mock_build.return_value = mock_srv
            run()
        mock_srv.run.assert_called_once_with(transport="stdio")


class TestMain:
    def test_main_entrypoint_calls_run(self, monkeypatch):
        """Verify __main__ invokes run() when executed as a module."""
        import runpy

        monkeypatch.setenv("ORC_AGENT_ROLE", "coder")
        with patch("orc.mcp.server.run") as mock_run:
            runpy.run_module("orc.mcp", run_name="__main__", alter_sys=True)
        mock_run.assert_called_once()
