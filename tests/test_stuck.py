"""Tests for the ``stuck`` task status feature."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orc.coordination.board._manager import TaskStatus
from orc.coordination.models import TaskComment, TaskEntry

# ---------------------------------------------------------------------------
# TaskStatus enum
# ---------------------------------------------------------------------------


class TestTaskStatusEnum:
    def test_stuck_in_enum(self):
        assert TaskStatus.STUCK == "stuck"

    def test_stuck_in_task_statuses(self):
        from orc.coordination.board._manager import TASK_STATUSES

        assert "stuck" in TASK_STATUSES


# ---------------------------------------------------------------------------
# MCP update_task_status
# ---------------------------------------------------------------------------


class TestUpdateTaskStatusAcceptsStuck:
    def test_stuck_is_valid(self, monkeypatch):
        monkeypatch.setenv("ORC_API_SOCKET", "/fake.sock")
        from unittest.mock import patch

        import orc.mcp.tools as _tools

        client_mock = MagicMock()
        client_mock.__enter__ = MagicMock(return_value=client_mock)
        client_mock.__exit__ = MagicMock(return_value=False)

        def _make_httpx_resp(status_code, body=None):
            r = MagicMock()
            r.status_code = status_code
            r.json.return_value = body or {}
            r.raise_for_status = MagicMock()
            return r

        client_mock.get.return_value = _make_httpx_resp(200, [{"name": "0002-foo.md"}])
        client_mock.put.return_value = _make_httpx_resp(204)

        with patch("orc.mcp.tools.get_client") as mock_gc:
            mock_gc.return_value = client_mock
            result = _tools.update_task_status("0002", "stuck")

        assert "stuck" in result

    def test_invalid_status_still_raises(self):
        import orc.mcp.tools as _tools

        with pytest.raises(ValueError, match="Invalid status"):
            _tools.update_task_status("0002", "limbo")


# ---------------------------------------------------------------------------
# Dispatcher: stuck tasks excluded, no planner dispatch, telegram once
# ---------------------------------------------------------------------------


class TestDispatcherStuck:
    def _make_dispatcher(self, tasks, messaging=None):
        """Return a Dispatcher with mocked services and given board tasks."""
        import orc.engine.dispatcher as _disp
        from orc.squad import SquadConfig

        squad = SquadConfig(
            planner=1,
            coder=1,
            qa=1,
            timeout_minutes=60,
            name="test",
            description="",
            _models={},
        )

        board = MagicMock()
        board.get_tasks.return_value = tasks
        board.get_pending_visions.return_value = []
        board.scan_todos.return_value = []
        board.get_blocked_tasks.return_value = []
        board.get_pending_reviews.return_value = []
        board.query_tasks.return_value = []
        board.is_empty.return_value = False

        messaging = messaging or MagicMock()
        workflow = MagicMock()
        workflow.derive_task_state.return_value = ("coder", "planned")
        agent = MagicMock()
        worktree = MagicMock()

        dispatcher = _disp.Dispatcher(
            squad,
            board=board,
            worktree=worktree,
            messaging=messaging,
            workflow=workflow,
            agent=agent,
        )
        return dispatcher, board, messaging

    def test_stuck_tasks_excluded_from_assignable(self):
        """stuck tasks are not included in assignable_tasks — no coder/QA dispatched."""

        stuck = TaskEntry(name="0001-foo.md", status="stuck")
        dispatcher, board, messaging = self._make_dispatcher([stuck])

        dispatched = dispatcher._dispatch(call_budget=5)
        assert dispatched == 0

    def test_stuck_does_not_trigger_planner(self):
        """stuck tasks do not count as planner work."""

        stuck = TaskEntry(name="0001-foo.md", status="stuck")
        dispatcher, board, messaging = self._make_dispatcher([stuck])

        dispatched = dispatcher._dispatch(call_budget=5)
        # No planner spawned either — stuck is not planner work
        assert dispatched == 0

    def test_stuck_sends_telegram_notification(self):
        """First time a stuck task is seen, a Telegram message is posted."""
        stuck = TaskEntry(name="0001-foo.md", status="stuck")
        dispatcher, board, messaging = self._make_dispatcher([stuck])

        dispatcher._dispatch(call_budget=5)

        messaging.post_boot_message.assert_called_once()
        call_args = messaging.post_boot_message.call_args
        assert "0001-foo.md" in call_args[0][1]
        assert "stuck" in call_args[0][1]

    def test_stuck_telegram_only_once(self):
        """Repeated dispatch cycles do not re-notify for the same stuck task."""
        stuck = TaskEntry(name="0001-foo.md", status="stuck")
        dispatcher, board, messaging = self._make_dispatcher([stuck])

        dispatcher._dispatch(call_budget=5)
        dispatcher._dispatch(call_budget=5)
        dispatcher._dispatch(call_budget=5)

        assert messaging.post_boot_message.call_count == 1

    def test_stuck_notification_includes_last_comment(self):
        """Notification message includes the last comment when present."""
        stuck = TaskEntry(
            name="0001-foo.md",
            status="stuck",
            comments=[
                TaskComment(
                    from_="coder-1",
                    text="stuck: need shell access to run migrations",
                    ts="2026-01-01T00:00:00Z",
                ),
            ],
        )
        dispatcher, board, messaging = self._make_dispatcher([stuck])

        dispatcher._dispatch(call_budget=5)

        call_args = messaging.post_boot_message.call_args
        assert "shell access" in call_args[0][1]

    def test_multiple_stuck_tasks_each_notified_once(self):
        """Each distinct stuck task triggers its own notification exactly once."""
        tasks = [
            TaskEntry(name="0001-foo.md", status="stuck"),
            TaskEntry(name="0002-bar.md", status="stuck"),
        ]
        dispatcher, board, messaging = self._make_dispatcher(tasks)

        dispatcher._dispatch(call_budget=5)
        dispatcher._dispatch(call_budget=5)

        assert messaging.post_boot_message.call_count == 2

    def test_any_work_returns_false_when_only_stuck(self):
        """_any_work() returns False when all tasks are stuck — loop can exit cleanly."""
        stuck = TaskEntry(name="0001-foo.md", status="stuck")
        dispatcher, board, _ = self._make_dispatcher([stuck])

        assert dispatcher._any_work() is False

    def test_any_work_returns_true_with_non_stuck_task(self):
        """_any_work() returns True when there is at least one non-stuck task."""
        tasks = [
            TaskEntry(name="0001-foo.md", status="stuck"),
            TaskEntry(name="0002-bar.md", status="in-progress"),
        ]
        dispatcher, board, _ = self._make_dispatcher(tasks)

        assert dispatcher._any_work() is True


# ---------------------------------------------------------------------------
# Status command
# ---------------------------------------------------------------------------


class TestStatusCommandStuck:
    def _run_status(self, monkeypatch, tasks):
        import orc.cli.status as _st
        from orc.coordination.models import Board

        monkeypatch.setattr(_st._board, "get_tasks", lambda: tasks)
        monkeypatch.setattr(
            _st._board_impl,
            "_read_board",
            lambda: Board(counter=0, tasks=tasks),
        )
        monkeypatch.setattr(_st, "_pending_visions", lambda: [])
        monkeypatch.setattr(_st, "_pending_reviews", lambda: [])
        monkeypatch.setattr(_st._ctx, "_scan_todos", lambda root: [])
        monkeypatch.setattr(_st, "_dev_ahead_of_main", lambda: 0)
        monkeypatch.setattr(
            _st, "load_squad", lambda n, orc_dir: (_ for _ in ()).throw(ValueError("no squad"))
        )
        monkeypatch.setattr(_st._ctx, "_role_symbol", lambda role: "")
        monkeypatch.setattr("orc.git.Git.branch_exists", lambda self, b: False)

        lines: list[str] = []

        def capture_echo(msg="", **kw):
            lines.append(str(msg))

        monkeypatch.setattr("typer.echo", capture_echo)
        _st._status()
        return "\n".join(lines)

    def test_stuck_warning_shown(self, monkeypatch, tmp_path):
        from dataclasses import replace as _replace

        import orc.config as _cfg

        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), orc_dir=tmp_path))
        tasks = [TaskEntry(name="0001-foo.md", status="stuck")]
        output = self._run_status(monkeypatch, tasks)
        assert "🔧" in output
        assert "0001-foo.md" in output

    def test_no_stuck_warning_when_no_stuck(self, monkeypatch, tmp_path):
        from dataclasses import replace as _replace

        import orc.config as _cfg

        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), orc_dir=tmp_path))
        tasks = [TaskEntry(name="0001-foo.md", status="in-progress")]
        output = self._run_status(monkeypatch, tasks)
        assert "🔧" not in output


# ---------------------------------------------------------------------------
# Status TUI kanban board
# ---------------------------------------------------------------------------


class TestStatusTuiStuckColumn:
    def _render_to_str(self, renderable) -> str:
        from io import StringIO

        from rich.console import Console

        buf = StringIO()
        console = Console(file=buf, width=200, highlight=False)
        console.print(renderable)
        return buf.getvalue()

    def test_stuck_task_appears_in_stuck_column(self, monkeypatch):
        import rich.table

        from orc.cli.tui.status_tui import _render_board
        from orc.coordination.client import BoardSnapshot

        snap = BoardSnapshot(
            visions=[],
            tasks=[TaskEntry(name="stuck-task.md", status="stuck")],
        )
        monkeypatch.setattr("orc.cli.tui.status_tui.get_board_snapshot", lambda: snap)
        result = _render_board()
        assert isinstance(result, rich.table.Table)
        rendered = self._render_to_str(result)
        assert "stuck-task.md" in rendered
        assert "🔧 Stuck" in rendered

    def test_stuck_task_not_in_in_progress_column(self, monkeypatch):
        """A stuck task must NOT bleed into the In progress column."""

        from orc.cli.tui.status_tui import _render_board
        from orc.coordination.client import BoardSnapshot

        snap = BoardSnapshot(
            visions=[],
            tasks=[
                TaskEntry(name="stuck-task.md", status="stuck"),
                TaskEntry(name="working-task.md", status="in-progress"),
            ],
        )
        monkeypatch.setattr("orc.cli.tui.status_tui.get_board_snapshot", lambda: snap)
        result = _render_board()
        rendered = self._render_to_str(result)

        # Both appear somewhere
        assert "stuck-task.md" in rendered
        assert "working-task.md" in rendered

        # Verify the Stuck column header is present
        assert "🔧 Stuck" in rendered


# ---------------------------------------------------------------------------
# Run TUI: stuck_tasks in RunState
# ---------------------------------------------------------------------------


class TestRunTuiStuckIndicator:
    def test_stuck_tasks_in_header_when_nonzero(self):
        from orc.cli.tui.run_tui import RunState, render

        state = RunState(
            agents=[],
            features_done=0,
            stuck_tasks=2,
            telegram_ok=False,
            backend="copilot",
            current_calls=0,
            max_calls=1,
        )
        from io import StringIO

        from rich.console import Console

        buf = StringIO()
        console = Console(file=buf, width=200, highlight=False)
        console.print(render(state))
        output = buf.getvalue()
        assert "🔧" in output
        assert "2 stuck" in output

    def test_no_stuck_indicator_when_zero(self):
        from orc.cli.tui.run_tui import RunState, render

        state = RunState(
            agents=[],
            features_done=0,
            stuck_tasks=0,
            telegram_ok=False,
            backend="copilot",
            current_calls=0,
            max_calls=1,
        )
        from io import StringIO

        from rich.console import Console

        buf = StringIO()
        console = Console(file=buf, width=200, highlight=False)
        console.print(render(state))
        output = buf.getvalue()
        assert "stuck" not in output


# ---------------------------------------------------------------------------
# Status command: planner note when stuck but not blocked
# ---------------------------------------------------------------------------


class TestStatusPlannerNoteStuck:
    def test_planner_note_idle_when_only_stuck(self, monkeypatch, tmp_path):
        """When tasks are stuck (not blocked), planner shows idle note."""
        from dataclasses import replace as _replace

        import orc.cli.status as _st
        import orc.config as _cfg
        from orc.coordination.models import Board
        from orc.squad import SquadConfig

        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), orc_dir=tmp_path))

        tasks = [TaskEntry(name="0001-foo.md", status="stuck")]
        monkeypatch.setattr(_st._board, "get_tasks", lambda: tasks)
        monkeypatch.setattr(_st._board_impl, "_read_board", lambda: Board(counter=0, tasks=tasks))
        monkeypatch.setattr(_st, "_pending_visions", lambda: [])
        monkeypatch.setattr(_st, "_pending_reviews", lambda: [])
        monkeypatch.setattr(_st._ctx, "_scan_todos", lambda root: [])
        monkeypatch.setattr(_st, "_dev_ahead_of_main", lambda: 0)
        monkeypatch.setattr(_st._wf, "features_in_dev_not_main", lambda: [])
        monkeypatch.setattr("orc.git.Git.branch_exists", lambda self, b: False)

        squad = SquadConfig(
            planner=1,
            coder=1,
            qa=1,
            timeout_minutes=60,
            name="test",
            description="",
            _models={},
        )
        monkeypatch.setattr(_st, "load_squad", lambda n, orc_dir: squad)
        monkeypatch.setattr(
            _st._wf,
            "_derive_task_state",
            lambda name, task=None: ("coder", "planned"),
        )

        lines: list[str] = []
        monkeypatch.setattr("typer.echo", lambda msg="", **kw: lines.append(str(msg)))
        _st._status()

        output = "\n".join(lines)
        assert "intervention, not planner" in output


# ---------------------------------------------------------------------------
# Status TUI: in-review branch label coverage (pre-existing gap)
# ---------------------------------------------------------------------------


class TestStatusTuiInReviewBranchLabel:
    def _render_to_str(self, renderable) -> str:
        from io import StringIO

        from rich.console import Console

        buf = StringIO()
        console = Console(file=buf, width=200, highlight=False)
        console.print(renderable)
        return buf.getvalue()

    def test_in_review_task_with_branch_shows_branch_in_label(self, monkeypatch):
        """in-review task with a branch attribute shows branch in the label."""
        import types

        from orc.cli.tui.status_tui import _render_board
        from orc.coordination.client import BoardSnapshot

        # Use a simple namespace so we can attach a `branch` attribute that
        # TaskEntry doesn't model (the code uses getattr with a default).
        fake_task = types.SimpleNamespace(
            name="0004-thing.md",
            status="in-review",
            assigned_to=None,
            branch="feat/0004-thing",
        )
        snap = BoardSnapshot(visions=[], tasks=[fake_task])  # type: ignore[arg-type]
        monkeypatch.setattr("orc.cli.tui.status_tui.get_board_snapshot", lambda: snap)
        result = _render_board()
        rendered = self._render_to_str(result)
        assert "feat/0004-thing" in rendered
