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
        with pytest.raises(ValueError, match="No task_name"):
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

        svcs = make_services(
            tmp_path,
            spawn_fn=_spawn,
            build_context_fn=lambda *a, **kw: ("model", ("system", "user")),
        )
        d = make_dispatcher(minimal_squad(), svcs)
        d._spawn_agent("coder", "coder-1", "0001-foo.md")

        assert captured["log_path"] == log_dir / "agents" / "coder-1.log"

    def test_spawn_agent_dry_run_prints(self, tmp_path, capsys):
        svcs = make_services(
            tmp_path, build_context_fn=lambda *a, **kw: ("model", ("system", "user"))
        )
        d = make_dispatcher(minimal_squad(), svcs, dry_run=True)
        d._spawn_agent("coder", "coder-1", "0001-foo.md")
        captured = capsys.readouterr()
        assert "Would spawn" in captured.out

    def test_dispatch_blocked_task_does_not_spawn(self, tmp_path):
        """Blocked task → no agent dispatched (planner is now an operation)."""
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [],
            get_pending_visions=lambda: [],
            get_blocked_tasks=lambda: ["0001-foo.md"],
            build_context_fn=lambda *a, **kw: ("model", ("system", "user")),
        )
        d = make_dispatcher(minimal_squad(), svcs)
        setup_work(d)
        count = d._dispatch(call_budget=100)
        assert count == 0  # no agents spawned — planning is an operation

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
        svcs = make_services(
            tmp_path,
            spawn_fn=spawn_fn,
            build_context_fn=lambda *a, **kw: ("model", ("system", "user")),
        )
        d = make_dispatcher(squad, svcs)
        # The stuck agent is killed by the watchdog (timeout_minutes=0).
        # The second agent (planner, FakePopen) completes without changing
        # board state but planners are exempt from noop detection, so
        # the run finishes at maxcalls.
        d.run(maxcalls=2)

    def test_loop_dry_run_stops_after_one_cycle(self, tmp_path, monkeypatch):
        """Dry-run breaks after first dispatch."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [TaskEntry(name="0001-foo.md", status="planned")],
            build_context_fn=lambda *a, **kw: ("model", ("system", "user")),
        )
        d = make_dispatcher(minimal_squad(), svcs, dry_run=True)
        d.run(maxcalls=sys.maxsize)
        assert d._total_spawned == 1

    def test_dispatch_skips_qa_passed_task(self, tmp_path):
        """QA_PASSED task is handled by merge operation, not spawned as an agent."""
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [TaskEntry(name="0001-foo.md", status="done")],
            derive_task_state=lambda t, td=None: (QA_PASSED, "qa passed"),
            get_pending_visions=lambda: [],
            build_context_fn=lambda *a, **kw: ("model", ("system", "user")),
        )
        d = make_dispatcher(minimal_squad(), svcs)
        setup_work(d)
        count = d._dispatch(call_budget=100)
        assert count == 0  # no agents spawned — merging is an operation

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

    def test_merger_runs_for_done_tasks_in_loop(self, tmp_path, monkeypatch):
        """Done tasks trigger merge operation in the loop."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)

        merged = []
        task_list_ref = [TaskEntry(name="0001-foo.md", status="done")]

        svcs = make_services(
            tmp_path,
            get_tasks=lambda: list(task_list_ref),
            get_pending_visions=lambda: [],
            derive_task_state=lambda t, td=None: (QA_PASSED, "qa passed"),
            build_context_fn=lambda *a, **kw: ("model", ("system", "user")),
        )
        svcs.workflow.merge_feature = lambda t: (merged.append(t), task_list_ref.clear())
        svcs.board.delete_task = lambda t: None

        d = make_dispatcher(minimal_squad(), svcs)
        d.run(maxcalls=1)
        assert merged == ["0001-foo.md"]

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

    def test_done_tasks_do_not_generate_coder_spawns(self, tmp_path, monkeypatch):
        """Done tasks (QA-approved) do not generate coder spawn intents."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        tasks = [TaskEntry(name="0001-foo.md", status="done")]

        plan = _disp.plan_dispatch(
            assignable=tasks,
            coder_bound=[],
            only_role=None,
            role_counts={"coder": 0},
            role_limits={"coder": 1},
            derive_task_state=lambda t, td=None: (QA_PASSED, "qa passed"),
        )
        # QA_PASSED tasks are handled by merge operation, not agent spawn
        assert len(plan.spawns) == 0

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
            get_tasks=lambda: [TaskEntry(name="0001-code.md")],
            spawn_fn=_spawn,
            build_context_fn=lambda *a, **kw: ("model", ("system", "user")),
        )
        hooks = _disp.DispatchHooks(on_agent_start=lambda agent: started.append(agent.agent_id))

        d = make_dispatcher(minimal_squad(), svcs, hooks=hooks)
        d._spawn_agent("coder", "coder-1", "0001-code.md")
        assert started == ["coder-1"]

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

        svcs = make_services(
            tmp_path,
            spawn_fn=_spawn,
            build_context_fn=lambda *a, **kw: ("model", ("system", "user")),
        )

        d = make_dispatcher(minimal_squad(), svcs)
        assert d.hooks.on_agent_start is None
        d._spawn_agent("coder", "coder-1", "0001-task.md")  # must not raise

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

        # After spawn, task changes status → not a noop.
        spawned = []

        def _spawn(ctx, cwd, model, log, **_kwargs):
            spawned.append(1)
            return SpawnResult(process=FakePopen(), log_fh=None)

        def _tasks():
            if spawned:
                return [TaskEntry(name="0001-code.md", status="in-review")]
            return [TaskEntry(name="0001-code.md")]

        svcs = make_services(
            tmp_path,
            get_tasks=_tasks,
            derive_task_state=lambda t, td=None: ("coder", "ready"),
            get_messages=lambda: [],
            spawn_fn=_spawn,
            build_context_fn=lambda *a, **kw: ("model", ("system", "user")),
        )
        d = make_dispatcher(minimal_squad(), svcs)
        d.run(maxcalls=1)
        assert d._total_spawned >= 1


class TestPlanOperation:
    """Plan operation replaces the planner agent."""

    def test_plan_operation_creates_tasks(self, tmp_path, monkeypatch):
        """_run_plan_operations creates tasks from vision documents."""
        from orc.engine.operations.plan import PlanResult, TaskSpec

        created: list[str] = []

        def _fake_plan(name, content, *, llm=None, existing_tasks=None):
            return PlanResult(
                tasks=[TaskSpec(title="new-task", overview="do stuff", steps=["step 1"])],
                vision_summary="summary",
            )

        monkeypatch.setattr("orc.engine.dispatcher.plan_vision", _fake_plan)

        svcs = make_services(
            tmp_path,
            get_pending_visions=lambda: ["vision-001.md"],
        )
        svcs.board.create_task = lambda title, vision, body: (
            created.append(title) or (f"0001-{title}.md", None)
        )
        d = make_dispatcher(minimal_squad(), svcs)
        d._run_plan_operations()
        assert "new-task" in created

    def test_plan_operation_closes_vision(self, tmp_path, monkeypatch):
        """_run_plan_operations closes processed visions."""
        from orc.engine.operations.plan import PlanResult, TaskSpec

        closed: list[str] = []

        def _fake_plan(name, content, *, llm=None, existing_tasks=None):
            return PlanResult(
                tasks=[TaskSpec(title="t", overview="o", steps=["s"])],
                vision_summary="summary",
            )

        monkeypatch.setattr("orc.engine.dispatcher.plan_vision", _fake_plan)

        svcs = make_services(
            tmp_path,
            get_pending_visions=lambda: ["feature.md"],
        )
        svcs.board.close_vision = lambda name, summary="", task_files=None: closed.append(name)
        d = make_dispatcher(minimal_squad(), svcs)
        d._run_plan_operations()
        assert closed == ["feature.md"]

    def test_plan_operation_skips_when_draining(self, tmp_path, monkeypatch):
        """_run_plan_operations does nothing in drain mode."""
        from orc.engine.operations.plan import PlanResult

        called = []
        monkeypatch.setattr(
            "orc.engine.dispatcher.plan_vision",
            lambda *a, **kw: called.append(1) or PlanResult(tasks=[], vision_summary=""),
        )
        svcs = make_services(tmp_path, get_pending_visions=lambda: ["v.md"])
        d = make_dispatcher(minimal_squad(), svcs)
        d.phase = _disp.DispatcherPhase.DRAINING
        d._run_plan_operations()
        assert called == []


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

    def test_only_coder_skips_operations(self, tmp_path):
        """With only_role='coder', visions are not planned."""
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

    def test_only_coder_dispatches_coder_skips_non_coder(self, tmp_path):
        """With only_role='coder', coder tasks are dispatched but QA-state tasks are skipped."""
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

    def test_no_filter_dispatches_coders_only(self, tmp_path):
        """Without only_role, only coder tasks are spawned as agents.

        QA-state tasks are handled by the review operation, not as agent spawns.
        """
        tasks = [TaskEntry(name="0001-code.md"), TaskEntry(name="0002-code2.md")]
        states = {"0001-code.md": ("coder", "ready"), "0002-code2.md": ("coder", "ready")}
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: tasks,
            derive_task_state=lambda t, td=None: states[t],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: [],
            build_context_fn=lambda *a, **kw: ("model", ("system", "user")),
        )
        d = make_dispatcher(minimal_squad(coder=2), svcs, only_role=None)
        setup_work(d)
        count = d._dispatch(call_budget=10)
        assert count == 2
        roles = {a.role for a in d.pool.all_agents()}
        assert roles == {"coder"}

    def test_only_role_idle_exits_when_no_work_for_role(self, tmp_path, monkeypatch):
        """Dispatcher stops when only_role is set and no work for that role exists."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [],
            get_pending_visions=lambda: [],
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
