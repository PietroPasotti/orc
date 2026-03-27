"""Tests for agent noop detection and shared context injection."""

from __future__ import annotations

import click
import pytest
from conftest import (
    make_agent,
    make_dispatcher,
    make_services,
    minimal_squad,
)

import orc.config as _cfg
import orc.engine.context as _ctx
import orc.engine.dispatcher as _disp
from orc.coordination.models import TaskEntry
from orc.engine.dispatcher import AgentNoopError, BoardSnapshot

# ---------------------------------------------------------------------------
# BoardSnapshot equality
# ---------------------------------------------------------------------------


class TestBoardSnapshot:
    def test_equal_snapshots(self):
        a = BoardSnapshot(
            task_statuses=(("t1.md", "planned"),),
            pending_visions=("v1.md",),
            blocked_tasks=(),
        )
        b = BoardSnapshot(
            task_statuses=(("t1.md", "planned"),),
            pending_visions=("v1.md",),
            blocked_tasks=(),
        )
        assert a == b

    def test_different_task_count(self):
        before = BoardSnapshot(
            task_statuses=(("t1.md", "planned"),),
            pending_visions=("v1.md",),
            blocked_tasks=(),
        )
        after = BoardSnapshot(
            task_statuses=(("t1.md", "planned"), ("t2.md", "planned")),
            pending_visions=("v1.md",),
            blocked_tasks=(),
        )
        assert before != after

    def test_different_vision_count(self):
        before = BoardSnapshot(
            task_statuses=(),
            pending_visions=("v1.md", "v2.md"),
            blocked_tasks=(),
        )
        after = BoardSnapshot(
            task_statuses=(),
            pending_visions=("v2.md",),
            blocked_tasks=(),
        )
        assert before != after

    def test_different_task_state_token(self):
        before = BoardSnapshot(
            task_statuses=(("t1.md", "planned"),),
            pending_visions=(),
            blocked_tasks=(),
            task_state_token="coder",
        )
        after = BoardSnapshot(
            task_statuses=(("t1.md", "in-review"),),
            pending_visions=(),
            blocked_tasks=(),
            task_state_token="qa",
        )
        assert before != after


# ---------------------------------------------------------------------------
# Noop detection in _handle_completion
# ---------------------------------------------------------------------------


class TestNoopDetection:
    """Verify that _handle_completion detects noops correctly."""

    def _make_dispatcher_with_snapshot(self, tmp_path, *, get_tasks=None, **kw):
        svcs = make_services(tmp_path, get_tasks=get_tasks, **kw)
        d = make_dispatcher(minimal_squad(), svcs)
        return d, svcs

    def test_planner_noop_not_detected_when_board_unchanged(self, tmp_path, monkeypatch):
        """Planner exits rc=0, board unchanged → NOT noop (planners exempt)."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        d, svcs = self._make_dispatcher_with_snapshot(tmp_path)

        agent = make_agent(tmp_path, role="planner", task=None)
        d.pool.add(agent)

        # Pre-spawn snapshot: no tasks, one pending vision (FakeBoard default).
        d._board_snapshots[agent.agent_id] = d._take_board_snapshot(agent.task_name)

        # Board state hasn't changed, but planners are exempt from noop detection.
        is_noop = d._handle_completion(agent, rc=0)
        assert is_noop is False

    def test_planner_not_noop_when_task_created(self, tmp_path, monkeypatch):
        """Planner exits rc=0, new task on board → NOT noop."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        # Board starts empty, then gets a task after the planner runs.
        call_count = 0

        def evolving_tasks():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return []
            return [TaskEntry(name="0001-new.md", status="planned")]

        d, svcs = self._make_dispatcher_with_snapshot(tmp_path, get_tasks=evolving_tasks)

        agent = make_agent(tmp_path, role="planner", task=None)
        d.pool.add(agent)
        d._board_snapshots[agent.agent_id] = d._take_board_snapshot(agent.task_name)

        is_noop = d._handle_completion(agent, rc=0)
        assert is_noop is False

    def test_planner_not_noop_when_vision_closed(self, tmp_path, monkeypatch):
        """Planner exits rc=0, one fewer pending vision → NOT noop."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        call_count = 0

        def evolving_visions():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return ["v1.md", "v2.md"]
            return ["v2.md"]

        d, svcs = self._make_dispatcher_with_snapshot(
            tmp_path, get_pending_visions=evolving_visions
        )

        agent = make_agent(tmp_path, role="planner", task=None)
        d.pool.add(agent)
        d._board_snapshots[agent.agent_id] = d._take_board_snapshot(agent.task_name)

        is_noop = d._handle_completion(agent, rc=0)
        assert is_noop is False

    def test_coder_noop_detected_when_status_unchanged(self, tmp_path, monkeypatch):
        """Coder exits rc=0, task status unchanged → noop."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        tasks = [TaskEntry(name="0001-foo.md", status="planned")]
        d, svcs = self._make_dispatcher_with_snapshot(
            tmp_path,
            get_tasks=lambda: tasks,
            derive_task_state=lambda t, td=None: ("coder", "not started"),
        )

        agent = make_agent(tmp_path, role="coder", task="0001-foo.md")
        d.pool.add(agent)
        d._board_snapshots[agent.agent_id] = d._take_board_snapshot(agent.task_name)

        is_noop = d._handle_completion(agent, rc=0)
        assert is_noop is True

    def test_coder_not_noop_when_status_changed(self, tmp_path, monkeypatch):
        """Coder exits rc=0, task routed to QA → NOT noop."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        call_count = 0

        def evolving_state(t, td=None):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return ("coder", "not started")
            return ("qa", "coder done")

        tasks = [TaskEntry(name="0001-foo.md", status="planned")]
        d, svcs = self._make_dispatcher_with_snapshot(
            tmp_path,
            get_tasks=lambda: tasks,
            derive_task_state=evolving_state,
        )

        agent = make_agent(tmp_path, role="coder", task="0001-foo.md")
        d.pool.add(agent)
        d._board_snapshots[agent.agent_id] = d._take_board_snapshot(agent.task_name)

        is_noop = d._handle_completion(agent, rc=0)
        assert is_noop is False

    def test_qa_noop_detected_when_unchanged(self, tmp_path, monkeypatch):
        """QA exits rc=0, task status unchanged → noop."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        tasks = [TaskEntry(name="0001-foo.md", status="in-review")]
        d, svcs = self._make_dispatcher_with_snapshot(
            tmp_path,
            get_tasks=lambda: tasks,
            derive_task_state=lambda t, td=None: ("qa", "in review"),
        )

        agent = make_agent(tmp_path, role="qa", task="0001-foo.md")
        d.pool.add(agent)
        d._board_snapshots[agent.agent_id] = d._take_board_snapshot(agent.task_name)

        is_noop = d._handle_completion(agent, rc=0)
        assert is_noop is True

    def test_failed_agent_not_noop(self, tmp_path, monkeypatch):
        """Agent exits rc!=0 → NOT classified as noop (it's a failure)."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        d, svcs = self._make_dispatcher_with_snapshot(tmp_path)

        agent = make_agent(tmp_path, role="planner", task=None)
        d.pool.add(agent)
        d._board_snapshots[agent.agent_id] = d._take_board_snapshot(agent.task_name)

        is_noop = d._handle_completion(agent, rc=1)
        assert is_noop is False

    def test_no_snapshot_skips_noop_check(self, tmp_path, monkeypatch):
        """Agent without a pre-spawn snapshot → noop check skipped."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        d, svcs = self._make_dispatcher_with_snapshot(tmp_path)

        agent = make_agent(tmp_path, role="planner", task=None)
        d.pool.add(agent)
        # Deliberately do NOT set d._board_snapshots[agent.agent_id].

        is_noop = d._handle_completion(agent, rc=0)
        assert is_noop is False


# ---------------------------------------------------------------------------
# Abort on noop in _poll_completed_agents
# ---------------------------------------------------------------------------


class TestNoopAbort:
    def test_poll_raises_noop_error(self, tmp_path, monkeypatch):
        """_poll_completed_agents raises AgentNoopError when noop detected."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = make_services(tmp_path)
        d = make_dispatcher(minimal_squad(), svcs)

        agent = make_agent(tmp_path, role="coder", task="0001-foo.md")
        d.pool.add(agent)
        d._board_snapshots[agent.agent_id] = d._take_board_snapshot(agent.task_name)

        with pytest.raises(AgentNoopError, match="coder-1"):
            d._poll_completed_agents()

    def test_run_exits_code_1_on_noop(self, tmp_path, monkeypatch):
        """run() catches AgentNoopError and exits with code 1."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = make_services(tmp_path)
        d = make_dispatcher(minimal_squad(), svcs)

        agent = make_agent(tmp_path, role="coder", task="0001-foo.md")
        d.pool.add(agent)
        d._board_snapshots[agent.agent_id] = d._take_board_snapshot(agent.task_name)

        with pytest.raises(click.exceptions.Exit) as exc_info:
            d.run()
        assert exc_info.value.exit_code == 1

    def test_planner_noop_does_not_abort(self, tmp_path, monkeypatch):
        """Planner unchanged board does NOT trigger AgentNoopError."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = make_services(tmp_path)
        d = make_dispatcher(minimal_squad(planner=1, coder=0, qa=0, merger=0), svcs)

        agent = make_agent(tmp_path, role="planner", task=None)
        d.pool.add(agent)
        d._board_snapshots[agent.agent_id] = d._take_board_snapshot(agent.task_name)

        # Should complete normally — planner noop does not abort.
        d._poll_completed_agents()


# ---------------------------------------------------------------------------
# Shared context injection
# ---------------------------------------------------------------------------


class TestSharedContext:
    def _make_board(self, orc_dir):
        from orc.coordination.state import BoardStateManager

        return BoardStateManager(orc_dir)

    def _setup(self, monkeypatch, *, with_shared: bool):
        """Set up config, agents dir, and mock git for context tests."""
        from dataclasses import replace as _replace

        import orc.messaging.telegram as tg

        cfg = _cfg.get()
        orc_dir = cfg.orc_dir
        agents_dir = orc_dir / "agents"
        work_dir = orc_dir / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "board.yaml").write_text("tasks: []\n")

        for role in ("planner", "coder", "qa"):
            role_dir = agents_dir / role
            role_dir.mkdir(parents=True, exist_ok=True)
            (role_dir / "_main.md").write_text(f"# {role}\n")

        if with_shared:
            shared_dir = agents_dir / "_shared"
            shared_dir.mkdir(exist_ok=True)
            (shared_dir / "_main.md").write_text("# Shared instructions\n")

        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(
                cfg,
                agents_dir=agents_dir,
                orc_dir=orc_dir,
                repo_root=cfg.repo_root,
                work_dir=work_dir,
                board_file=work_dir / "board.yaml",
            ),
        )
        monkeypatch.setattr("orc.git.Git.ensure_worktree", lambda self, worktree, branch: None)
        monkeypatch.setattr(tg, "_get_messages", lambda limit=100: [])
        return orc_dir

    def test_shared_context_injected_when_present(self, tmp_path, monkeypatch):
        """build_agent_context includes _shared/_main.md when it exists."""
        orc_dir = self._setup(monkeypatch, with_shared=True)
        board = self._make_board(orc_dir)

        ctx = _ctx.build_agent_context("planner", board, "planner-1", plain=True)
        assert "_shared/_main.md" in ctx
        assert "shared instructions for all agents" in ctx.lower()

    def test_shared_context_not_injected_when_absent(self, tmp_path, monkeypatch):
        """build_agent_context omits _shared reference when the dir doesn't exist."""
        orc_dir = self._setup(monkeypatch, with_shared=False)
        board = self._make_board(orc_dir)

        ctx = _ctx.build_agent_context("planner", board, "planner-1", plain=True)
        assert "_shared/_main.md" not in ctx

    def test_shared_path_before_role_path(self, tmp_path, monkeypatch):
        """Shared instructions path appears before role-specific path."""
        orc_dir = self._setup(monkeypatch, with_shared=True)
        board = self._make_board(orc_dir)

        ctx = _ctx.build_agent_context("planner", board, "planner-1", plain=True)
        shared_pos = ctx.index("_shared/_main.md")
        role_pos = ctx.index("planner/_main.md")
        assert shared_pos < role_pos, "Shared context should come before role context"

    def test_shared_context_works_for_all_roles(self, tmp_path, monkeypatch):
        """Shared context is injected for coder and qa too."""
        orc_dir = self._setup(monkeypatch, with_shared=True)
        board = self._make_board(orc_dir)

        cfg = _cfg.get()
        monkeypatch.setattr(_cfg.Config, "feature_branch", lambda self, t: "feat/0001-test")
        monkeypatch.setattr(
            _cfg.Config, "feature_worktree_path", lambda self, t: cfg.repo_root / "feat"
        )

        for role in ("planner", "coder", "qa"):
            task = "0001-test.md" if role != "planner" else None
            ctx = _ctx.build_agent_context(role, board, f"{role}-1", task_name=task, plain=True)
            assert "_shared/_main.md" in ctx, f"Shared context missing for {role}"
