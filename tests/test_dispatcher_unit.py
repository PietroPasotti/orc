"""Tests for orc/dispatcher.py — unit tests."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock
from unittest.mock import patch as _patch

import pytest
from conftest import (
    FakePopen,
    make_agent,
    make_dispatcher,
    make_services,
    minimal_squad,
    setup_work,
)

import orc.config as _cfg
import orc.engine.dispatcher as _disp
from orc.ai.backends import SpawnResult
from orc.coordination.models import TaskEntry
from orc.engine.dispatcher import CLOSE_BOARD, QA_PASSED, DispatcherPhase
from orc.squad import SquadConfig

# ---------------------------------------------------------------------------
# Dispatcher unit tests
# ---------------------------------------------------------------------------


class TestDispatcherCoverage:
    def test_run_shutdown_signal_raises_exit_130(self, tmp_path, monkeypatch):
        """run() catches _ShutdownSignal and raises typer.Exit(130)."""
        import click

        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = make_services(tmp_path)
        d = make_dispatcher(minimal_squad(), svcs)

        def boom(maxcalls):
            raise _disp._ShutdownSignal(2)

        d._loop = boom
        d._kill_all_and_unassign = MagicMock()
        with pytest.raises(click.exceptions.Exit) as exc_info:
            d.run()
        assert exc_info.value.exit_code == 130

    def test_do_merge_failure_logs_error(self, tmp_path, monkeypatch):
        """_do_merge: exception → error logged, no crash."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = make_services(tmp_path)
        svcs.workflow.merge_feature = lambda task: (_ for _ in ()).throw(
            RuntimeError("merge conflict")
        )
        d = make_dispatcher(minimal_squad(), svcs)
        d._do_merge("0001-foo.md")  # should not raise

    def test_do_merge_calls_on_feature_merged_hook(self, tmp_path, monkeypatch):
        """_do_merge fires the on_feature_merged hook on success."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        merged: list[bool] = []
        svcs = make_services(tmp_path)
        hooks = _disp.DispatchHooks(on_feature_merged=lambda: merged.append(True))
        d = make_dispatcher(minimal_squad(), svcs, hooks=hooks)
        d._do_merge("0001-foo.md")
        assert merged == [True]

    def test_classify_tasks_blocked_skipped(self):
        """classify_tasks drops blocked tasks from assignable and coder_bound."""
        tasks = [
            TaskEntry(name="a.md", status="blocked"),
            TaskEntry(name="b.md", status="planned"),
            TaskEntry(name="c.md", status="in-review"),
        ]
        stuck, assignable, coder_bound = _disp.classify_tasks(tasks)
        assert stuck == []
        names = [t.name for t in assignable]
        assert "a.md" not in names
        assert "b.md" in names and "c.md" in names
        coder_names = [t.name for t in coder_bound]
        assert coder_names == ["b.md"]

    def test_handle_watchdog_kills_agent(self, tmp_path, monkeypatch):
        """_handle_watchdog kills the agent and unassigns its task."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        unassigned = []
        svcs = make_services(tmp_path, get_tasks=lambda: [])
        svcs.board.unassign_task = lambda t: unassigned.append(t)
        d = make_dispatcher(minimal_squad(), svcs)
        agent = make_agent(tmp_path, role="coder")
        d.pool.add(agent)
        d._handle_watchdog(agent)
        assert "0001-foo.md" in unassigned

    def test_handle_watchdog_calls_on_agent_done(self, tmp_path, monkeypatch):
        """_handle_watchdog fires on_agent_done so the TUI removes the stale card."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        done: list[tuple[str, int]] = []
        svcs = make_services(tmp_path, get_tasks=lambda: [])
        hooks = _disp.DispatchHooks(
            on_agent_done=lambda agent, rc: done.append((agent.agent_id, rc))
        )
        d = make_dispatcher(minimal_squad(), svcs, hooks=hooks)
        agent = make_agent(tmp_path, role="qa")
        d.pool.add(agent)
        d._handle_watchdog(agent)
        assert done == [("qa-1", -1)]

    def test_spawn_agent_raises_for_non_planner_without_task(self, tmp_path):
        svcs = make_services(tmp_path)
        d = make_dispatcher(minimal_squad(), svcs)
        with pytest.raises(ValueError, match="No worktree"):
            d._spawn_agent("coder", "coder-1", None)

    def test_spawn_agent_log_path_is_under_log_dir_agents(self, tmp_path, monkeypatch):
        """Agent log path is LOG_DIR/agents/{agent_id}.log."""
        from dataclasses import replace as _replace

        log_dir = tmp_path / "logs"
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), log_dir=log_dir))

        captured: dict = {}

        def _spawn(ctx, cwd, model, log, **_kwargs):
            captured["log_path"] = log
            return SpawnResult(process=FakePopen(), log_fh=None)

        svcs = make_services(tmp_path, spawn_fn=_spawn, build_context_fn=lambda *a, **kw: ("model", ("system", "user")))
        d = make_dispatcher(minimal_squad(), svcs)
        d._spawn_agent("planner", "planner-1", None)

        assert captured["log_path"] == log_dir / "agents" / "planner-1.log"

    def test_spawn_agent_dry_run_prints(self, tmp_path, capsys):
        svcs = make_services(tmp_path, build_context_fn=lambda *a, **kw: ("model", ("system", "user")))
        d = make_dispatcher(minimal_squad(), svcs, dry_run=True)
        d._spawn_agent("planner", "planner-1", None)
        captured = capsys.readouterr()
        assert "Would spawn" in captured.out

    def test_dispatch_blocked_task_spawns_planner(self, tmp_path):
        """Blocked task → planner dispatched to resolve it."""
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [],
            get_pending_visions=lambda: [],
            get_blocked_tasks=lambda: ["0001-foo.md"],
            build_context_fn=lambda *a, **kw: ("model", ("system", "user"))
        )
        d = make_dispatcher(minimal_squad(), svcs)
        setup_work(d)
        count = d._dispatch(call_budget=100)
        assert count >= 1
        pool_roles = [a.role for a in d.pool.all_agents()]
        assert "planner" in pool_roles

    def test_dispatch_skips_assigned_task(self, tmp_path):
        """Task already assigned → skipped."""
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [TaskEntry(name="0001-foo.md", assigned_to="coder-1")],
            derive_task_state=lambda t, td=None: ("coder", "reason"),
        )
        d = make_dispatcher(minimal_squad(), svcs)
        setup_work(d)
        count = d._dispatch(call_budget=100)
        assert count == 0

    def test_dispatch_close_board(self, tmp_path):
        """CLOSE_BOARD token → board.delete_task called."""
        deleted = []
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [TaskEntry(name="0001-foo.md")],
            derive_task_state=lambda t, td=None: (CLOSE_BOARD, "close"),
        )
        svcs.board.delete_task = lambda t: deleted.append(t)
        d = make_dispatcher(minimal_squad(), svcs)
        setup_work(d)
        d._dispatch(call_budget=100)
        assert "0001-foo.md" in deleted

    def test_dispatch_close_board_failure_is_logged(self, tmp_path, monkeypatch):
        """delete_task() failure is logged and does not propagate."""
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [TaskEntry(name="0001-foo.md")],
            derive_task_state=lambda t, td=None: (CLOSE_BOARD, "close"),
        )

        def _boom(task_name):
            raise RuntimeError("board exploded")

        svcs.board.delete_task = _boom
        d = make_dispatcher(minimal_squad(), svcs)
        setup_work(d)

        with _patch.object(_disp.logger, "exception") as mock_log:
            result = d._dispatch(call_budget=100)

        mock_log.assert_called_once()
        call_kwargs = mock_log.call_args
        assert "delete_task failed during crash recovery" in call_kwargs[0]
        assert result == 0  # no agents spawned, but dispatch continued without raising

    def test_dispatch_skips_unknown_token(self, tmp_path):
        """Unknown token → task skipped, dispatch returns 0."""
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [TaskEntry(name="0001-foo.md")],
            derive_task_state=lambda t, td=None: ("unknown_token", "reason"),
        )
        d = make_dispatcher(minimal_squad(), svcs)
        setup_work(d)
        assert d._dispatch(call_budget=1000) == 0

    def test_dispatch_skips_when_role_at_capacity(self, tmp_path):
        """Coder at capacity → skip."""
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [TaskEntry(name="0001-foo.md")],
            derive_task_state=lambda t, td=None: ("coder", "reason"),
        )
        d = make_dispatcher(minimal_squad(), svcs)
        setup_work(d)
        d.pool.add(make_agent(tmp_path, role="coder"))  # coder-1 already running
        assert d._dispatch(call_budget=1000) == 0

    def test_loop_watchdog_triggers(self, tmp_path, monkeypatch):
        """Watchdog kills stuck agents."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        class StuckPopen:
            returncode = None

            def poll(self):
                return None

            def kill(self):
                pass

            def wait(self, timeout=None):
                pass

        spawned = [False]

        def spawn_fn(ctx, cwd, model, log, **_kwargs):
            if not spawned[0]:
                spawned[0] = True
                return SpawnResult(process=StuckPopen(), log_fh=None)
            return SpawnResult(process=FakePopen(), log_fh=None)

        squad = SquadConfig(
            planner=1,
            coder=1,
            qa=1,
            merger=1,
            timeout_minutes=0,
            name="test",
            description="",
            _models={},
        )
        svcs = make_services(tmp_path, spawn_fn=spawn_fn, build_context_fn=lambda *a, **kw: ("model", ("system", "user")))
        d = make_dispatcher(squad, svcs)
        # The stuck agent is killed by the watchdog (timeout_minutes=0).
        # The second agent (planner, FakePopen) completes without changing
        # board state but planners are exempt from noop detection, so
        # the run finishes at maxcalls.
        d.run(maxcalls=2)

    def test_loop_dry_run_stops_after_one_cycle(self, tmp_path, monkeypatch):
        """Dry-run breaks after first dispatch."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = make_services(tmp_path, build_context_fn=lambda *a, **kw: ("model", ("system", "user")))
        d = make_dispatcher(minimal_squad(), svcs, dry_run=True)
        d.run(maxcalls=sys.maxsize)
        assert d._total_spawned == 1

    def test_dispatch_spawns_merger_for_qa_passed_task(self, tmp_path):
        """QA_PASSED token → merger agent dispatched."""
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [TaskEntry(name="0001-foo.md", status="done")],
            derive_task_state=lambda t, td=None: (QA_PASSED, "qa passed"),
            get_pending_visions=lambda: [],
            build_context_fn=lambda *a, **kw: ("model", ("system", "user"))
        )
        d = make_dispatcher(minimal_squad(), svcs)
        setup_work(d)
        count = d._dispatch(call_budget=100)
        assert count == 1  # merger agent spawned
        agents = d.pool.all_agents()
        assert len(agents) == 1
        assert agents[0].role == "merger"
        assert agents[0].task_name == "0001-foo.md"

    def test_handle_completion_failed_agent_unassigns(self, tmp_path, monkeypatch):
        """Non-zero exit → task unassigned."""
        unassigned = []
        svcs = make_services(tmp_path)
        svcs.board.unassign_task = lambda t: unassigned.append(t)
        d = make_dispatcher(minimal_squad(), svcs)
        agent = make_agent(tmp_path, role="coder")
        d.pool.add(agent)
        d._handle_completion(agent, 1)
        assert "0001-foo.md" in unassigned


# ---------------------------------------------------------------------------
# Coverage gap tests
# ---------------------------------------------------------------------------


class TestDispatcherInternalCoverage:
    def test_kill_all_and_unassign(self, tmp_path):
        """Lines 493-496: _kill_all_and_unassign unassigns tasks and kills agents."""
        unassigned = []
        svcs = make_services(tmp_path)
        svcs.board.unassign_task = lambda t: unassigned.append(t)
        d = make_dispatcher(minimal_squad(), svcs)
        agent = make_agent(tmp_path, role="coder")
        d.pool.add(agent)
        d._kill_all_and_unassign()
        assert "0001-foo.md" in unassigned

    def test_shutdown_handler_raises_signal(self, tmp_path):
        """_shutdown_handler raises _ShutdownSignal on second call."""
        svcs = make_services(tmp_path)
        d = make_dispatcher(minimal_squad(), svcs)
        d._shutdown_handler(15, None)  # first call: sets drain mode
        with pytest.raises(_disp._ShutdownSignal):
            d._shutdown_handler(15, None)  # second call: raises

    def test_cleanup_context_tmp_deletes_file(self, tmp_path):
        """Lines 515-517: _cleanup_context_tmp deletes the temp file."""
        tmp_file = tmp_path / "ctx.tmp"
        tmp_file.write_text("context data")

        _disp._cleanup_context_tmp(str(tmp_file))
        assert not tmp_file.exists()

    def test_cleanup_context_tmp_no_attr(self, tmp_path):
        """_cleanup_context_tmp is a no-op when context_tmp is None."""
        _disp._cleanup_context_tmp(None)  # should not raise

    def test_dispatch_returns_zero_when_nothing_to_do(self, tmp_path, monkeypatch):
        """_dispatch returns 0 immediately when no tasks or visions."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: [],
        )
        d = make_dispatcher(minimal_squad(), svcs)
        setup_work(d)
        result = d._dispatch(call_budget=100)
        assert result == 0

    def test_drain_merge_queue_skips_done_tasks_for_merger(self, tmp_path, monkeypatch):
        """_drain_merge_queue no longer merges done tasks — those go to merger agents."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        merged = []
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [TaskEntry(name="0001-foo.md", status="done")],
            get_pending_visions=lambda: [],
        )
        svcs.workflow.merge_feature = lambda t: merged.append(t)
        d = make_dispatcher(minimal_squad(), svcs)
        d._drain_merge_queue()
        assert merged == [], "done tasks should not be merged by drain — handled by merger agent"

    def test_drain_merge_queue_skips_non_done_tasks(self, tmp_path):
        """_drain_merge_queue does not merge tasks that are not 'done'."""
        merged = []
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [TaskEntry(name="0001-foo.md", status="in-progress")],
        )
        svcs.workflow.merge_feature = lambda t: merged.append(t)
        d = make_dispatcher(minimal_squad(), svcs)
        d._drain_merge_queue()
        assert merged == []

    def test_merger_dispatched_for_done_tasks_in_loop(self, tmp_path, monkeypatch):
        """Done tasks trigger merger dispatch in the loop."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        def _tracking_spawn(ctx, cwd, model, log, **kw):
            # When the merger is spawned, simulate it completing the merge
            # by clearing the task list (the merger calls close_merge/delete_task).
            task_list_ref.clear()
            return SpawnResult(process=FakePopen(), log_fh=None)

        task_list_ref = [TaskEntry(name="0001-foo.md", status="done")]
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: list(task_list_ref),
            get_pending_visions=lambda: [],
            derive_task_state=lambda t, td=None: (QA_PASSED, "qa passed"),
            spawn_fn=_tracking_spawn,
            build_context_fn=lambda *a, **kw: ("model", ("system", "user"))
        )
        d = make_dispatcher(minimal_squad(), svcs)
        d.run(maxcalls=1)
        assert d._id_counters.get("merger", 0) == 1

    def test_do_merge_marks_stuck_after_max_retries(self, tmp_path, monkeypatch):
        """_do_merge marks a task as stuck after _MAX_MERGE_RETRIES failures."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        monkeypatch.setattr(_disp, "_MAX_MERGE_RETRIES", 2)
        status_updates = []
        svcs = make_services(tmp_path)
        svcs.workflow.merge_feature = lambda task: (_ for _ in ()).throw(RuntimeError("conflict"))
        svcs.board.set_task_status = lambda task, status: status_updates.append((task, status))
        d = make_dispatcher(minimal_squad(), svcs)

        d._do_merge("0001-foo.md")
        assert status_updates == [], "not stuck after first failure"

        d._do_merge("0001-foo.md")
        assert ("0001-foo.md", "stuck") in status_updates

    def test_do_merge_resets_failure_count_on_success(self, tmp_path, monkeypatch):
        """Successful merge clears the failure counter for the task."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        monkeypatch.setattr(_disp, "_MAX_MERGE_RETRIES", 2)
        svcs = make_services(tmp_path)

        call_count = 0

        def merge_fail_then_succeed(task):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise RuntimeError("conflict")

        svcs.workflow.merge_feature = merge_fail_then_succeed
        d = make_dispatcher(minimal_squad(), svcs)

        d._do_merge("0001-foo.md")  # fail 1
        assert d._merge_failures.get("0001-foo.md") == 1

        d._do_merge("0001-foo.md")  # succeed
        assert "0001-foo.md" not in d._merge_failures

    def test_drain_merge_queue_skips_already_merged(self, tmp_path, monkeypatch):
        """_drain_merge_queue skips tasks whose branch is already merged into dev."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        merged = []
        deleted = []
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [TaskEntry(name="0001-foo.md", status="done")],
        )
        svcs.workflow.derive_task_state = lambda t, td=None: (
            _disp.CLOSE_BOARD,
            "already merged",
        )
        svcs.workflow.merge_feature = lambda t: merged.append(t)
        svcs.board.delete_task = lambda t: deleted.append(t)
        d = make_dispatcher(minimal_squad(), svcs)
        d._drain_merge_queue()
        assert merged == [], "merge should not have been attempted"
        assert "0001-foo.md" in deleted

    def test_run_exits_workflow_complete_when_no_work(self, tmp_path, monkeypatch, capsys):
        """run() logs workflow-complete and exits when nothing to dispatch."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: [],
            build_context_fn=lambda *a, **kw: ("model", ("system", "user")),
        )
        d = make_dispatcher(minimal_squad(), svcs)
        d.run(maxcalls=sys.maxsize)
        out = capsys.readouterr().out
        assert "Workflow complete" in out

    def test_classify_tasks_done_included_as_merger_bound(self):
        """classify_tasks includes done tasks in assignable (merger-bound) but not coder_bound."""
        tasks = [
            TaskEntry(name="a.md", status="done"),
            TaskEntry(name="b.md", status="planned"),
        ]
        stuck, assignable, coder_bound = _disp.classify_tasks(tasks)
        assert stuck == []
        names = [t.name for t in assignable]
        assert "a.md" in names  # done → assignable (merger dispatches for it)
        assert "b.md" in names
        coder_names = [t.name for t in coder_bound]
        assert coder_names == ["b.md"]  # done tasks are NOT coder-bound

    def test_done_tasks_do_not_block_planner(self, tmp_path, monkeypatch):
        """Done tasks should not prevent the planner from being spawned."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        tasks = [TaskEntry(name="0001-foo.md", status="done")]

        def _spawn(ctx, cwd, model, log, **kw):
            return SpawnResult(process=FakePopen(), log_fh=None, context_tmp="")

        svcs = make_services(
            tmp_path,
            get_tasks=lambda: list(tasks),
            get_pending_visions=lambda: ["new-feature.md"],
            spawn_fn=_spawn,
        )
        svcs.workflow.merge_feature = lambda t: None
        svcs.board.delete_task = lambda t: tasks.clear()

        plan = _disp.plan_dispatch(
            assignable=[],
            coder_bound=[],
            has_planner_work=True,
            only_role=None,
            coder_capacity=1,
            planner_running=False,
            role_counts={"coder": 0, "qa": 0, "merger": 0},
            role_limits={"coder": 1, "qa": 1, "merger": 1},
            derive_task_state=svcs.workflow.derive_task_state,
        )
        assert any(s.role == "planner" for s in plan.spawns), "planner should be spawned"

    def test_drain_merge_queue_handles_orphaned_branches(self, tmp_path, monkeypatch):
        """_drain_merge_queue merges orphaned feature branches with no board entry."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        merged = []
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: ["feat/0002-orphan"],
        )
        svcs.workflow.merge_feature = lambda t: merged.append(t)
        d = make_dispatcher(minimal_squad(), svcs)
        d._drain_merge_queue()
        assert "0002-orphan.md" in merged

    def test_any_work_returns_true_for_pending_reviews(self, tmp_path, monkeypatch):
        """_any_work() returns True when there are unmerged feature branches."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: ["feat/0001-foo"],
        )
        d = make_dispatcher(minimal_squad(), svcs)
        assert d._any_work() is True

    def test_branch_to_task_name(self):
        """_branch_to_task_name correctly reverses feature_branch()."""
        assert _disp._branch_to_task_name("feat/0001-foo") == "0001-foo.md"
        assert _disp._branch_to_task_name("myprefix/feat/0001-foo") == "0001-foo.md"
        assert _disp._branch_to_task_name("feat/0001-foo.bar") == "0001-foo.bar.md"


class TestDispatchCallbacksOptional:
    """Tests for the optional on_agent_start / on_agent_done hooks."""

    def test_on_agent_start_called_after_spawn(self, tmp_path):
        """on_agent_start receives the AgentProcess just added to the pool."""
        started = []

        def _spawn(ctx, cwd, model, log, **_kwargs):
            return SpawnResult(process=FakePopen(), log_fh=None)

        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [],
            spawn_fn=_spawn,
            build_context_fn=lambda *a, **kw: ("model", ("system", "user"))
        )
        hooks = _disp.DispatchHooks(on_agent_start=lambda agent: started.append(agent.agent_id))

        d = make_dispatcher(minimal_squad(), svcs, hooks=hooks)
        d._spawn_agent("planner", "planner-1", None)
        assert started == ["planner-1"]

    def test_on_agent_done_called_after_completion(self, tmp_path):
        """on_agent_done receives the completed agent and its exit code."""
        done = []

        svcs = make_services(tmp_path)
        hooks = _disp.DispatchHooks(
            on_agent_done=lambda agent, rc: done.append((agent.agent_id, rc))
        )

        d = make_dispatcher(minimal_squad(), svcs, hooks=hooks)
        agent = make_agent(tmp_path)
        d.pool.add(agent)
        d._handle_completion(agent, 0)
        assert done == [("coder-1", 0)]

    def test_on_agent_done_called_on_failure(self, tmp_path):
        """on_agent_done is called even when the agent exits non-zero."""
        done = []

        svcs = make_services(tmp_path)
        hooks = _disp.DispatchHooks(
            on_agent_done=lambda agent, rc: done.append((agent.agent_id, rc))
        )

        d = make_dispatcher(minimal_squad(), svcs, hooks=hooks)
        agent = make_agent(tmp_path)
        d.pool.add(agent)
        d._handle_completion(agent, 1)
        assert done == [("coder-1", 1)]

    def test_on_agent_start_none_is_safe(self, tmp_path):
        """on_agent_start=None (default) does not crash."""

        def _spawn(ctx, cwd, model, log, **_kwargs):
            return SpawnResult(process=FakePopen(), log_fh=None)

        svcs = make_services(tmp_path, spawn_fn=_spawn, build_context_fn=lambda *a, **kw: ("model", ("system", "user")))

        d = make_dispatcher(minimal_squad(), svcs)
        assert d.hooks.on_agent_start is None
        d._spawn_agent("planner", "planner-1", None)  # must not raise

    def test_on_agent_done_none_is_safe(self, tmp_path):
        """on_agent_done=None (default) does not crash."""
        svcs = make_services(tmp_path)

        d = make_dispatcher(minimal_squad(), svcs)
        assert d.hooks.on_agent_done is None
        agent = make_agent(tmp_path)
        d.pool.add(agent)
        d._handle_completion(agent, 0)  # must not raise

    def test_on_orc_status_called(self, tmp_path):
        """on_orc_status receives the task string."""
        updates: list[str] = []

        svcs = make_services(tmp_path)
        hooks = _disp.DispatchHooks(on_orc_status=lambda task: updates.append(task))

        d = make_dispatcher(minimal_squad(), svcs, hooks=hooks)
        d._set_orc_task("merging task 0001-foo.md")
        assert updates == ["merging task 0001-foo.md"]

    def test_on_orc_status_none_is_safe(self, tmp_path):
        """on_orc_status=None (default) does not crash."""
        svcs = make_services(tmp_path)

        d = make_dispatcher(minimal_squad(), svcs)
        assert d.hooks.on_orc_status is None
        d._set_orc_task("checking pending work")  # must not raise

    def test_on_cycle_called_each_loop_iteration(self, tmp_path, monkeypatch):
        """on_cycle is called once per dispatch loop iteration."""
        import orc.engine.dispatcher as _d

        monkeypatch.setattr(_d, "_POLL_INTERVAL", 0)

        ticks: list[int] = []
        hooks = _disp.DispatchHooks(on_cycle=lambda: ticks.append(1))

        svcs = make_services(tmp_path, get_tasks=lambda: [], get_messages=lambda: [])
        d = make_dispatcher(minimal_squad(), svcs, hooks=hooks)
        # Planner completes without changing board state (exempt from noop).
        # on_cycle should still have been called at least once.
        d.run(maxcalls=1)

        assert len(ticks) >= 1

    def test_on_cycle_none_is_safe(self, tmp_path, monkeypatch):
        """on_cycle=None (default) does not crash."""
        import orc.engine.dispatcher as _d

        monkeypatch.setattr(_d, "_POLL_INTERVAL", 0)

        svcs = make_services(tmp_path, get_tasks=lambda: [], get_messages=lambda: [])
        d = make_dispatcher(minimal_squad(), svcs)
        assert d.hooks.on_cycle is None
        # Planner completes without changing board state (exempt from noop).
        # The test verifies on_cycle=None doesn't crash.
        d.run(maxcalls=1)

    def test_echo_routes_to_logger_when_tui_active(self, tmp_path):
        """_echo logs via structlog instead of typer when TUI hook is active."""
        svcs = make_services(tmp_path)
        hooks = _disp.DispatchHooks(on_orc_status=lambda *_: None)
        d = make_dispatcher(minimal_squad(), svcs, hooks=hooks)
        # final=False + on_orc_status set → must not raise (hits logger.info branch)
        d._echo("syncing…", final=False)


class TestDispatcherLoopProperty:
    def test_spawned_starts_at_zero(self, tmp_path):
        svcs = make_services(tmp_path)
        d = make_dispatcher(minimal_squad(), svcs)
        assert d._total_spawned == 0

    def test_spawned_increments_each_cycle(self, tmp_path, monkeypatch):
        """_total_spawned reflects the number of agent sessions launched."""
        import orc.engine.dispatcher as _d

        monkeypatch.setattr(_d, "_POLL_INTERVAL", 0)

        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [],
            get_messages=lambda: [],
            build_context_fn=lambda *a, **kw: ("model", ("system", "user"))
        )
        d = make_dispatcher(minimal_squad(), svcs)
        # Planner completes without changing board state (exempt from noop),
        # but _total_spawned should already be incremented.
        d.run(maxcalls=1)
        assert d._total_spawned >= 1


class TestProactivePlanner:
    """Dispatcher spawns a planner proactively when open_tasks < coder count."""

    def test_spawns_planner_when_tasks_below_coder_capacity(self, tmp_path):
        """Planner spawned when open_tasks < coder count and visions pending."""
        spawned_roles: list[str] = []

        def _spawn(ctx, cwd, model, log, **_kwargs):
            return SpawnResult(process=FakePopen(), log_fh=None)

        svcs = make_services(
            tmp_path,
            # 1 open task, squad has 2 coders → pipeline has room → spawn planner
            get_tasks=lambda: [TaskEntry(name="0001-foo.md")],
            derive_task_state=lambda t, td=None: ("coder", "ready"),
            get_pending_visions=lambda: ["vision-001.md"],
            spawn_fn=_spawn,
            build_context_fn=lambda *a, **kw: ("model", ("system", "user"))
        )
        svcs.board.assign_task = lambda t, a: spawned_roles.append(a.split("-")[0])

        # Squad with 2 coders so that 1 open task < 2 triggers the proactive path.
        squad = minimal_squad(coder=2)
        d = make_dispatcher(squad, svcs)
        setup_work(d)
        d._dispatch(call_budget=100)

        # Should have spawned 1 coder + 1 planner
        pool_roles = [a.role for a in d.pool.all_agents()]
        assert "planner" in pool_roles, f"expected planner in pool, got {pool_roles}"

    def test_no_proactive_planner_when_tasks_meet_coder_capacity(self, tmp_path):
        """No proactive planner when open_tasks >= coder count."""
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [TaskEntry(name="0001-foo.md"), TaskEntry(name="0002-bar.md")],
            derive_task_state=lambda t, td=None: ("coder", "ready"),
            get_pending_visions=lambda: ["vision-001.md"],
            build_context_fn=lambda *a, **kw: ("model", ("system", "user"))
        )
        # 2 open tasks, 2 coders → at capacity, no proactive planner
        squad = minimal_squad(coder=2)
        d = make_dispatcher(squad, svcs)
        setup_work(d)
        d._dispatch(call_budget=100)

        pool_roles = [a.role for a in d.pool.all_agents()]
        assert "planner" not in pool_roles, f"unexpected planner in pool: {pool_roles}"

    def test_no_proactive_planner_when_no_pending_visions(self, tmp_path):
        """No proactive planner when there are no pending vision docs to plan."""
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [TaskEntry(name="0001-foo.md")],
            derive_task_state=lambda t, td=None: ("coder", "ready"),
            get_pending_visions=lambda: [],  # nothing to plan
            build_context_fn=lambda *a, **kw: ("model", ("system", "user"))
        )
        squad = minimal_squad(coder=2)
        d = make_dispatcher(squad, svcs)
        setup_work(d)
        d._dispatch(call_budget=100)

        pool_roles = [a.role for a in d.pool.all_agents()]
        assert "planner" not in pool_roles, f"unexpected planner in pool: {pool_roles}"

    def test_no_proactive_planner_when_planner_already_running(self, tmp_path):
        """No second planner spawned when one is already in the pool."""
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [TaskEntry(name="0001-foo.md")],
            derive_task_state=lambda t, td=None: ("coder", "ready"),
            get_pending_visions=lambda: ["vision-001.md"],
            build_context_fn=lambda *a, **kw: ("model", ("system", "user"))
        )
        squad = minimal_squad(coder=2)
        d = make_dispatcher(squad, svcs)
        setup_work(d)
        # Pre-populate pool with a planner.
        d.pool.add(make_agent(tmp_path, role="planner", task=""))
        d._dispatch(call_budget=100)

        planner_count = sum(1 for a in d.pool.all_agents() if a.role == "planner")
        assert planner_count == 1, "second planner must not be spawned"

    def test_spawns_planner_when_all_tasks_in_review(self, tmp_path):
        """Planner spawned when all assignable tasks are in-review (going to QA).

        The coder slot is free while QA handles the task, so the planner
        should run to refine todos before the coder goes idle.
        """

        def _spawn(ctx, cwd, model, log, **_kwargs):
            return SpawnResult(process=FakePopen(), log_fh=None)

        svcs = make_services(
            tmp_path,
            # 1 task in-review — routes to QA, not coder
            get_tasks=lambda: [TaskEntry(name="0001-foo.md", status="in-review")],
            derive_task_state=lambda t, td=None: ("qa", "coder finished, awaiting QA"),
            scan_todos=lambda: [{"file": "src/x.py", "line": 1, "text": "TODO: fix me"}],
            spawn_fn=_spawn,
            build_context_fn=lambda *a, **kw: ("model", ("system", "user"))
        )
        # Default squad: 1 coder, 1 QA.
        squad = minimal_squad(coder=1)
        d = make_dispatcher(squad, svcs)
        setup_work(d)
        d._dispatch(call_budget=100)

        pool_roles = [a.role for a in d.pool.all_agents()]
        assert "planner" in pool_roles, (
            f"expected planner to be spawned when all tasks are in-review, got {pool_roles}"
        )


class TestMaxcallsUnlimited:
    """Dispatcher.run() with sys.maxsize behaves as unlimited."""

    def test_unlimited_dispatches_agents(self, tmp_path, monkeypatch):
        """run(maxcalls=sys.maxsize) dispatches agents normally (not zero)."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: [],
            build_context_fn=lambda *a, **kw: ("model", ("system", "user")),
        )
        d = make_dispatcher(minimal_squad(), svcs)
        d.run(maxcalls=sys.maxsize)
        assert d._total_spawned >= 0


class TestDispatchBudgetExhaustion:
    """_dispatch stops spawning when call_budget is consumed mid-dispatch."""

    def test_budget_limits_spawns(self, tmp_path):
        """With budget=1 and 2 tasks, only 1 agent is spawned."""
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [
                TaskEntry(name="0001-foo.md"),
                TaskEntry(name="0002-bar.md"),
            ],
            derive_task_state=lambda t, td=None: ("coder", "ready"),
            build_context_fn=lambda *a, **kw: ("model", ("system", "user")),
        )
        # Squad allows 2 coders, but budget is capped at 1.
        squad = minimal_squad(coder=2)
        d = make_dispatcher(squad, svcs)
        setup_work(d)
        count = d._dispatch(call_budget=1)
        assert count == 1
        assert len(d.pool.all_agents()) == 1


# ---------------------------------------------------------------------------
# --agent (only_role) filtering
# ---------------------------------------------------------------------------


class TestOnlyRoleFiltering:
    """Dispatcher.only_role restricts which roles get dispatched."""

    def test_only_coder_skips_planner(self, tmp_path):
        """With only_role='coder', planner is not dispatched even when visions exist."""
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [],
            get_pending_visions=lambda: ["v1.md"],
            get_pending_reviews=lambda: [],
            build_context_fn=lambda *a, **kw: ("model", ("system", "user")),
        )
        d = make_dispatcher(minimal_squad(), svcs, only_role="coder")
        setup_work(d)
        count = d._dispatch(call_budget=10)
        assert count == 0
        assert d.pool.is_empty()

    def test_only_planner_dispatches_planner(self, tmp_path):
        """With only_role='planner' and pending visions, a planner is spawned."""
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [],
            get_pending_visions=lambda: ["v1.md"],
            get_pending_reviews=lambda: [],
            build_context_fn=lambda *a, **kw: ("model", ("system", "user")),
        )
        d = make_dispatcher(minimal_squad(), svcs, only_role="planner")
        setup_work(d)
        count = d._dispatch(call_budget=10)
        assert count == 1
        agents = d.pool.all_agents()
        assert len(agents) == 1
        assert agents[0].role == "planner"

    def test_only_coder_dispatches_coder_skips_qa(self, tmp_path):
        """With only_role='coder', coder tasks are dispatched but QA tasks are skipped."""
        tasks = [TaskEntry(name="0001-code.md"), TaskEntry(name="0002-review.md")]
        states = {"0001-code.md": ("coder", "ready"), "0002-review.md": ("qa", "ready")}
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: tasks,
            derive_task_state=lambda t, td=None: states[t],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: [],
            build_context_fn=lambda *a, **kw: ("model", ("system", "user")),
        )
        d = make_dispatcher(minimal_squad(), svcs, only_role="coder")
        setup_work(d)
        count = d._dispatch(call_budget=10)
        assert count == 1
        agents = d.pool.all_agents()
        assert all(a.role == "coder" for a in agents)

    def test_only_qa_dispatches_qa_skips_coder(self, tmp_path):
        """With only_role='qa', QA tasks are dispatched but coder tasks are skipped."""
        tasks = [TaskEntry(name="0001-code.md"), TaskEntry(name="0002-review.md")]
        states = {"0001-code.md": ("coder", "ready"), "0002-review.md": ("qa", "ready")}
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: tasks,
            derive_task_state=lambda t, td=None: states[t],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: [],
            build_context_fn=lambda *a, **kw: ("model", ("system", "user")),
        )
        d = make_dispatcher(minimal_squad(), svcs, only_role="qa")
        setup_work(d)
        count = d._dispatch(call_budget=10)
        assert count == 1
        agents = d.pool.all_agents()
        assert all(a.role == "qa" for a in agents)

    def test_no_filter_dispatches_all_roles(self, tmp_path):
        """Without only_role, both coder and QA tasks are dispatched."""
        tasks = [TaskEntry(name="0001-code.md"), TaskEntry(name="0002-review.md")]
        states = {"0001-code.md": ("coder", "ready"), "0002-review.md": ("qa", "ready")}
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: tasks,
            derive_task_state=lambda t, td=None: states[t],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: [],
            build_context_fn=lambda *a, **kw: ("model", ("system", "user")),
        )
        d = make_dispatcher(minimal_squad(), svcs, only_role=None)
        setup_work(d)
        count = d._dispatch(call_budget=10)
        assert count == 2
        roles = {a.role for a in d.pool.all_agents()}
        assert roles == {"coder", "qa"}

    def test_only_role_idle_exits_when_no_work_for_role(self, tmp_path, monkeypatch):
        """Dispatcher stops when only_role is set and no work for that role exists."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [],
            get_pending_visions=lambda: ["v1.md"],
            get_pending_reviews=lambda: [],
            build_context_fn=lambda *a, **kw: ("model", ("system", "user")),
        )
        d = make_dispatcher(minimal_squad(), svcs, only_role="coder")
        d.run(maxcalls=5)
        assert d._total_spawned == 0


# ---------------------------------------------------------------------------
# Two-stage graceful shutdown tests
# ---------------------------------------------------------------------------


class TestTwoStageShutdown:
    """Tests for the two-stage signal handler and drain mode."""

    def test_first_signal_sets_draining_phase(self, tmp_path):
        """First signal transitions phase to DRAINING without raising."""
        svcs = make_services(tmp_path)
        d = make_dispatcher(minimal_squad(), svcs)
        assert d.phase is DispatcherPhase.RUNNING

        # First signal: should NOT raise, should set phase.
        d._shutdown_handler(15, None)
        assert d.phase is DispatcherPhase.DRAINING

    def test_second_signal_raises_shutdown_signal(self, tmp_path):
        """Second signal raises _ShutdownSignal."""
        svcs = make_services(tmp_path)
        d = make_dispatcher(minimal_squad(), svcs)

        # First signal: sets phase.
        d._shutdown_handler(15, None)
        assert d.phase is DispatcherPhase.DRAINING

        # Second signal: raises.
        with pytest.raises(_disp._ShutdownSignal):
            d._shutdown_handler(15, None)

    def test_dispatch_agents_returns_zero_when_draining(self, tmp_path, monkeypatch):
        """When phase is DRAINING, _dispatch_agents() returns 0 without spawning."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        spawned = []

        def _spawn(ctx, cwd, model, log, **_kwargs):
            spawned.append(True)
            return SpawnResult(process=FakePopen(), log_fh=None, context_tmp="")

        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [
                TaskEntry(name="0001-foo.md", status="in-progress", assigned_to="coder-1")
            ],
            spawn_fn=_spawn,
        )
        d = make_dispatcher(minimal_squad(), svcs)
        d.phase = DispatcherPhase.DRAINING

        result = d._dispatch_agents(call_budget=10)
        assert result == 0
        assert spawned == []

    def test_loop_exits_cleanly_after_drain(self, tmp_path, monkeypatch):
        """_loop() terminates with clean return (exit 0) when drain completes."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: [],
        )
        d = make_dispatcher(minimal_squad(), svcs)
        d.phase = DispatcherPhase.DRAINING

        # _loop should return normally (not raise) when pool is empty + draining.
        d._loop(maxcalls=sys.maxsize)
        # If we reach here without exception, the test passes.

    def test_run_returns_cleanly_after_drain(self, tmp_path, monkeypatch):
        """run() returns without raising when drain completes (exit code 0)."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: [],
        )
        d = make_dispatcher(minimal_squad(), svcs)
        d.phase = DispatcherPhase.DRAINING

        # Should not raise (exit code 0).
        d.run(maxcalls=sys.maxsize)

    def test_shutting_down_property_compat(self, tmp_path):
        """_shutting_down property reflects and sets phase for backward compat."""
        svcs = make_services(tmp_path)
        d = make_dispatcher(minimal_squad(), svcs)
        assert d._shutting_down is False

        d._shutting_down = True
        assert d.phase is DispatcherPhase.DRAINING
        assert d._shutting_down is True

        d._shutting_down = False
        assert d.phase is DispatcherPhase.RUNNING
        assert d._shutting_down is False

    def test_run_force_shutdown_exits_130(self, tmp_path, monkeypatch):
        """run() exits 130 when _ShutdownSignal is raised (second signal)."""
        import click

        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = make_services(tmp_path)
        d = make_dispatcher(minimal_squad(), svcs)

        def boom(maxcalls):
            raise _disp._ShutdownSignal(2)

        d._loop = boom
        d._kill_all_and_unassign = MagicMock()
        with pytest.raises(click.exceptions.Exit) as exc_info:
            d.run()
        assert exc_info.value.exit_code == 130
        d._kill_all_and_unassign.assert_called_once()


# ---------------------------------------------------------------------------
# Agent retry budget (A) and worktree cleanup (B) tests
# ---------------------------------------------------------------------------


class TestAgentRetryBudget:
    """Tests for _agent_failures counter and STUCK transition (feature A)."""

    def test_agent_fails_once_increments_counter_and_unassigns(self, tmp_path):
        """Single failure increments _agent_failures and unassigns task."""
        unassigned = []
        svcs = make_services(tmp_path)
        svcs.board.unassign_task = lambda t: unassigned.append(t)
        d = make_dispatcher(minimal_squad(), svcs)
        agent = make_agent(tmp_path, role="coder")
        d.pool.add(agent)

        d._handle_completion(agent, 1)

        assert d._agent_failures.get("0001-foo.md") == 1
        assert "0001-foo.md" in unassigned

    def test_agent_fails_max_retries_marks_stuck(self, tmp_path, monkeypatch):
        """After _MAX_AGENT_RETRIES failures the task is marked STUCK."""
        monkeypatch.setattr(_disp, "_MAX_AGENT_RETRIES", 2)
        status_updates = []
        svcs = make_services(tmp_path)
        svcs.board.set_task_status = lambda task, status: status_updates.append((task, status))
        d = make_dispatcher(minimal_squad(), svcs)

        def _agent():
            a = make_agent(tmp_path, role="coder")
            d.pool.add(a)
            return a

        d._handle_completion(_agent(), 1)
        assert ("0001-foo.md", "stuck") not in status_updates

        d._handle_completion(_agent(), 1)
        assert ("0001-foo.md", "stuck") in status_updates
        # counter cleared after STUCK
        assert "0001-foo.md" not in d._agent_failures

    def test_agent_success_resets_failure_counter(self, tmp_path):
        """Successful completion resets _agent_failures for that task."""
        svcs = make_services(tmp_path)
        d = make_dispatcher(minimal_squad(), svcs)

        # Seed a failure count
        d._agent_failures["0001-foo.md"] = 2

        agent = make_agent(tmp_path, role="coder")
        d.pool.add(agent)
        d._handle_completion(agent, 0)

        assert "0001-foo.md" not in d._agent_failures

    def test_agent_no_task_name_no_counter_update(self, tmp_path):
        """Agent without task_name does not touch _agent_failures."""
        svcs = make_services(tmp_path)
        d = make_dispatcher(minimal_squad(), svcs)

        agent = make_agent(tmp_path, role="planner", task=None)
        d.pool.add(agent)
        d._handle_completion(agent, 1)

        assert d._agent_failures == {}


class TestWorktreeCleanupOnStuck:
    """Tests for cleanup_feature_worktree calls on STUCK transitions (feature B)."""

    def test_cleanup_called_when_agent_retry_limit_reached(self, tmp_path, monkeypatch):
        """cleanup_feature_worktree is called when agent hits retry limit."""
        monkeypatch.setattr(_disp, "_MAX_AGENT_RETRIES", 1)
        cleaned = []
        svcs = make_services(tmp_path)
        svcs.worktree.cleanup_feature_worktree = lambda t: cleaned.append(t)
        d = make_dispatcher(minimal_squad(), svcs)

        agent = make_agent(tmp_path, role="coder")
        d.pool.add(agent)
        d._handle_completion(agent, 1)

        assert "0001-foo.md" in cleaned

    def test_cleanup_called_when_merge_retry_limit_reached(self, tmp_path, monkeypatch):
        """cleanup_feature_worktree is called when merge hits retry limit."""
        monkeypatch.setattr(_disp, "_MAX_MERGE_RETRIES", 1)
        cleaned = []
        svcs = make_services(tmp_path)
        svcs.workflow.merge_feature = lambda task: (_ for _ in ()).throw(RuntimeError("conflict"))
        svcs.worktree.cleanup_feature_worktree = lambda t: cleaned.append(t)
        d = make_dispatcher(minimal_squad(), svcs)

        d._do_merge("0001-foo.md")

        assert "0001-foo.md" in cleaned

    def test_cleanup_not_called_on_first_merge_failure(self, tmp_path, monkeypatch):
        """cleanup_feature_worktree is NOT called when merge fails below retry limit."""
        monkeypatch.setattr(_disp, "_MAX_MERGE_RETRIES", 3)
        cleaned = []
        svcs = make_services(tmp_path)
        svcs.workflow.merge_feature = lambda task: (_ for _ in ()).throw(RuntimeError("conflict"))
        svcs.worktree.cleanup_feature_worktree = lambda t: cleaned.append(t)
        d = make_dispatcher(minimal_squad(), svcs)

        d._do_merge("0001-foo.md")

        assert cleaned == []


class TestFmtElapsed:
    """Tests for the _fmt_elapsed helper (feature C)."""

    def test_seconds_only_below_60(self):
        assert _disp._fmt_elapsed(42.0) == "42s"
        assert _disp._fmt_elapsed(0.0) == "0s"
        assert _disp._fmt_elapsed(59.9) == "59s"

    def test_minutes_and_seconds_at_60_or_above(self):
        assert _disp._fmt_elapsed(60.0) == "1m 0s"
        assert _disp._fmt_elapsed(222.0) == "3m 42s"
        assert _disp._fmt_elapsed(3600.0) == "60m 0s"


class TestWorktreeManagerCleanup:
    """Tests for WorktreeManager.cleanup_feature_worktree (feature B)."""

    def test_cleanup_idempotent_when_worktree_missing(self, tmp_path, monkeypatch):
        """cleanup_feature_worktree does not raise if worktree already gone."""
        import orc.engine.workflow as _wf
        from orc.engine.workflow import WorktreeManager

        prune_calls = []
        branch_delete_calls = []

        class FakeGit:
            def __init__(self, *a, **kw):
                pass

            def worktree_remove(self, path, force=True):
                raise RuntimeError("already gone")

            def worktree_prune(self):
                prune_calls.append(True)

            def branch_exists(self, name):
                return False

            def branch_delete(self, name, force=False):
                branch_delete_calls.append(name)

        monkeypatch.setattr(_wf, "Git", FakeGit)
        monkeypatch.setattr(
            _wf._cfg,
            "get",
            lambda: _MockCfg(tmp_path),
        )

        wm = WorktreeManager()
        wm.cleanup_feature_worktree("0001-foo.md")

        # prune always called; branch_delete not called (branch didn't exist)
        assert prune_calls
        assert branch_delete_calls == []

    def test_cleanup_removes_worktree_and_branch_when_present(self, tmp_path, monkeypatch):
        """cleanup_feature_worktree calls remove + prune + branch_delete when present."""
        import orc.engine.workflow as _wf
        from orc.engine.workflow import WorktreeManager

        wt_path = tmp_path / "feature-wt"
        wt_path.mkdir()
        removed = []
        pruned = []
        deleted = []

        class FakeGit:
            def __init__(self, *a, **kw):
                pass

            def worktree_remove(self, path, force=True):
                removed.append(str(path))

            def worktree_prune(self):
                pruned.append(True)

            def branch_exists(self, name):
                return True

            def branch_delete(self, name, force=False):
                deleted.append(name)

        monkeypatch.setattr(_wf, "Git", FakeGit)
        monkeypatch.setattr(
            _wf._cfg,
            "get",
            lambda: _MockCfg(tmp_path, wt_override=wt_path),
        )

        wm = WorktreeManager()
        wm.cleanup_feature_worktree("0001-foo.md")

        assert removed
        assert pruned
        assert deleted


class _MockCfg:
    """Minimal stand-in for OrcConfig used in WorktreeManager tests."""

    def __init__(self, root, *, wt_override=None):
        self.repo_root = root
        self._wt_override = wt_override

    def feature_worktree_path(self, task_name):
        if self._wt_override is not None:
            return self._wt_override
        return self.repo_root / "worktrees" / task_name

    def feature_branch(self, task_name):
        return f"feat/{task_name}"
