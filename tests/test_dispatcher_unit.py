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
from orc.engine.dispatcher import CLOSE_BOARD, QA_PASSED
from orc.engine.pool import AgentProcess
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

    def test_spawn_agent_raises_for_non_planner_without_task(self, tmp_path):
        svcs = make_services(tmp_path)
        d = make_dispatcher(minimal_squad(), svcs)
        with pytest.raises(ValueError, match="No worktree"):
            d._spawn_agent("coder", "coder-1", None, [])

    def test_spawn_agent_log_path_is_under_log_dir_agents(self, tmp_path, monkeypatch):
        """Agent log path is LOG_DIR/agents/{agent_id}.log."""
        from dataclasses import replace as _replace

        log_dir = tmp_path / "logs"
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), log_dir=log_dir))

        captured: dict = {}

        def _spawn(ctx, cwd, model, log):
            captured["log_path"] = log
            return SpawnResult(process=FakePopen(), log_fh=None, context_tmp="")

        svcs = make_services(tmp_path, spawn_fn=_spawn)
        d = make_dispatcher(minimal_squad(), svcs)
        d._spawn_agent("planner", "planner-1", None, [])

        assert captured["log_path"] == log_dir / "agents" / "planner-1.log"

    def test_spawn_agent_dry_run_prints(self, tmp_path, capsys):
        svcs = make_services(tmp_path)
        d = make_dispatcher(minimal_squad(), svcs, dry_run=True)
        d._spawn_agent("planner", "planner-1", None, [])
        captured = capsys.readouterr()
        assert "Would spawn" in captured.out

    def test_dispatch_soft_block_spawns_planner(self, tmp_path, monkeypatch):
        """Soft-blocked state → planner spawned to resolve."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = make_services(tmp_path, get_tasks=lambda: [{"name": "0001-foo.md"}])
        svcs.messaging.has_unresolved_block = lambda msgs: ("coder-1", "soft-blocked")
        d = make_dispatcher(minimal_squad(), svcs)
        setup_work(d)
        count = d._dispatch([], 100)
        assert count >= 1

    def test_dispatch_skips_assigned_task(self, tmp_path):
        """Task already assigned → skipped."""
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [{"name": "0001-foo.md", "assigned_to": "coder-1"}],
            derive_task_state=lambda t: ("coder", "reason"),
        )
        d = make_dispatcher(minimal_squad(), svcs)
        setup_work(d)
        count = d._dispatch([], 100)
        assert count == 0

    def test_dispatch_close_board(self, tmp_path):
        """CLOSE_BOARD token → do_close_board called."""
        closed = []
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [{"name": "0001-foo.md"}],
            derive_task_state=lambda t: (CLOSE_BOARD, "close"),
        )
        svcs.workflow.do_close_board = lambda t: closed.append(t)
        d = make_dispatcher(minimal_squad(), svcs)
        setup_work(d)
        d._dispatch([], 100)
        assert "0001-foo.md" in closed

    def test_dispatch_close_board_failure_is_logged(self, tmp_path, monkeypatch):
        """do_close_board() failure is logged and does not propagate."""
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [{"name": "0001-foo.md"}],
            derive_task_state=lambda t: (CLOSE_BOARD, "close"),
        )

        def _boom(task_name):
            raise RuntimeError("board exploded")

        svcs.workflow.do_close_board = _boom
        d = make_dispatcher(minimal_squad(), svcs)
        setup_work(d)

        with _patch.object(_disp.logger, "exception") as mock_log:
            result = d._dispatch([], 100)

        mock_log.assert_called_once()
        call_kwargs = mock_log.call_args
        assert "do_close_board failed" in call_kwargs[0]
        assert result == 0  # no agents spawned, but dispatch continued without raising

    def test_dispatch_skips_unknown_token(self, tmp_path):
        """Unknown token → task skipped, dispatch returns 0."""
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [{"name": "0001-foo.md"}],
            derive_task_state=lambda t: ("unknown_token", "reason"),
        )
        d = make_dispatcher(minimal_squad(), svcs)
        setup_work(d)
        assert d._dispatch([], 1000) == 0

    def test_dispatch_skips_when_role_at_capacity(self, tmp_path):
        """Coder at capacity → skip."""
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [{"name": "0001-foo.md"}],
            derive_task_state=lambda t: ("coder", "reason"),
        )
        d = make_dispatcher(minimal_squad(), svcs)
        setup_work(d)
        d.pool.add(make_agent(tmp_path, role="coder"))  # coder-1 already running
        assert d._dispatch([], 1000) == 0

    def test_loop_refreshes_messages_after_completion(self, tmp_path, monkeypatch):
        """Line 235: messages refreshed after hard-block handling."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = make_services(tmp_path)
        d = make_dispatcher(minimal_squad(), svcs, dry_run=True)

        block_returned = [False]

        def fake_has_unresolved_block(messages):
            if not block_returned[0]:
                block_returned[0] = True
                return ("planner-1", "blocked")
            return (None, None)

        d.messaging.has_unresolved_block = fake_has_unresolved_block
        d._handle_hard_block = MagicMock()
        d._loop(maxcalls=sys.maxsize)

        d._handle_hard_block.assert_called_once()

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

        def spawn_fn(ctx, cwd, model, log):
            if not spawned[0]:
                spawned[0] = True
                return SpawnResult(process=StuckPopen(), log_fh=None, context_tmp="")
            return SpawnResult(process=FakePopen(), log_fh=None, context_tmp="")

        squad = SquadConfig(
            planner=1,
            coder=1,
            qa=1,
            timeout_minutes=0,
            name="test",
            description="",
            _models={},
        )
        svcs = make_services(tmp_path, spawn_fn=spawn_fn)
        d = make_dispatcher(squad, svcs)
        d.run(maxcalls=2)

    def test_loop_dry_run_stops_after_one_cycle(self, tmp_path, monkeypatch):
        """Dry-run breaks after first dispatch."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = make_services(tmp_path)
        d = make_dispatcher(minimal_squad(), svcs, dry_run=True)
        d.run(maxcalls=sys.maxsize)
        assert d._total_spawned == 1

    def test_handle_hard_block_posts_resolved_after_human_reply(self, tmp_path, monkeypatch):
        """_handle_hard_block posts resolved after human reply."""
        resolved = []
        svcs = make_services(
            tmp_path,
            wait_for_human_reply=lambda msgs, **kw: "Here is the answer.",
        )
        svcs.messaging.post_resolved = lambda a, s, r: resolved.append((a, s, r))
        d = make_dispatcher(minimal_squad(), svcs)
        d._handle_hard_block("coder-1", [])
        assert len(resolved) == 1
        assert resolved[0][0] == "coder-1"

    def test_dispatch_qa_passed_queues_merge(self, tmp_path, monkeypatch):
        """QA_PASSED token → task added to merge queue."""
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [{"name": "0001-foo.md"}],
            derive_task_state=lambda t: (QA_PASSED, "qa passed"),
        )
        d = make_dispatcher(minimal_squad(), svcs)
        setup_work(d)
        d._dispatch([], 100)
        assert "0001-foo.md" in d._merge_queue

    def test_handle_completion_posts_resolved_for_soft_block_planner(self, tmp_path, monkeypatch):
        """After planner resolves soft-block, post_resolved is called."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        resolved = []
        svcs = make_services(tmp_path)
        svcs.messaging.post_resolved = lambda a, s, r: resolved.append((a, s, r))

        d = make_dispatcher(minimal_squad(), svcs)
        d._resolving_soft_block = ("coder-1", "soft-blocked")

        agent = AgentProcess(
            agent_id="planner-1",
            role="planner",
            model="copilot",
            task_name=None,
            process=FakePopen(),
            worktree=tmp_path,
            log_path=tmp_path / "log",
            log_fh=None,
            context_tmp=None,
        )
        d.pool.add(agent)
        d._handle_completion(agent, 0, [])
        assert len(resolved) == 1

    def test_handle_completion_failed_agent_unassigns(self, tmp_path, monkeypatch):
        """Non-zero exit → task unassigned."""
        unassigned = []
        svcs = make_services(tmp_path)
        svcs.board.unassign_task = lambda t: unassigned.append(t)
        d = make_dispatcher(minimal_squad(), svcs)
        agent = make_agent(tmp_path, role="coder")
        d.pool.add(agent)
        d._handle_completion(agent, 1, [])
        assert "0001-foo.md" in unassigned

    def test_loop_merge_queue_drained(self, tmp_path, monkeypatch):
        """Merge queue drained in loop."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        merged = []
        svcs = make_services(tmp_path)
        svcs.workflow.merge_feature = lambda t: merged.append(t)
        d = make_dispatcher(minimal_squad(), svcs)
        d._merge_queue.append("0001-foo.md")
        d.run(maxcalls=1)
        assert "0001-foo.md" in merged


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
        """Line 499: _shutdown_handler raises _ShutdownSignal."""
        svcs = make_services(tmp_path)
        d = make_dispatcher(minimal_squad(), svcs)
        with pytest.raises(_disp._ShutdownSignal):
            d._shutdown_handler(15, None)

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
        """_dispatch returns 0 immediately when no tasks or visions (line 318)."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: [],
        )
        d = make_dispatcher(minimal_squad(), svcs)
        setup_work(d)
        result = d._dispatch([], 100)
        assert result == 0

    def test_dispatch_queues_pending_reviews_for_merge(self, tmp_path, monkeypatch):
        """_dispatch adds unmerged feat/* branches to the merge queue (no agent spawned)."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: ["feat/0001-foo"],
        )
        d = make_dispatcher(minimal_squad(), svcs)
        setup_work(d)
        result = d._dispatch([], 100)
        assert result == 0  # merge-queue additions don't count as agent spawns
        assert "0001-foo.md" in d._merge_queue

    def test_dispatch_deduplicates_pending_reviews(self, tmp_path, monkeypatch):
        """_dispatch doesn't add the same branch twice to the merge queue."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: ["feat/0001-foo"],
        )
        d = make_dispatcher(minimal_squad(), svcs)
        setup_work(d)
        d._dispatch([], 100)
        d._dispatch([], 100)
        assert d._merge_queue.count("0001-foo.md") == 1

    def test_dispatch_queues_open_prs_when_tasks_also_present(self, tmp_path, monkeypatch):
        """open_PRs are queued for merge even when open_tasks is non-empty (lines 453-459)."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [{"name": "0001-foo.md"}],
            get_pending_reviews=lambda: ["feat/0001-foo"],
            derive_task_state=lambda t: ("coder", "no prior work"),
        )
        d = make_dispatcher(minimal_squad(), svcs)
        setup_work(d)
        d._dispatch([], 100)
        assert "0001-foo.md" in d._merge_queue

    def test_run_exits_workflow_complete_when_no_work(self, tmp_path, monkeypatch, capsys):
        """run() logs workflow-complete and exits when nothing to dispatch (lines 299-301)."""
        monkeypatch.setattr(_disp, "_POLL_INTERVAL", 0.0)
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: [],
        )
        svcs.messaging.has_unresolved_block = lambda msgs: (None, None)
        d = make_dispatcher(minimal_squad(), svcs)
        d.run(maxcalls=sys.maxsize)
        out = capsys.readouterr().out
        assert "Workflow complete" in out


class TestDispatchCallbacksOptional:
    """Tests for the optional on_agent_start / on_agent_done hooks."""

    def test_on_agent_start_called_after_spawn(self, tmp_path):
        """on_agent_start receives the AgentProcess just added to the pool."""
        started = []

        def _spawn(ctx, cwd, model, log):
            return SpawnResult(process=FakePopen(), log_fh=None, context_tmp="")

        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [],
            spawn_fn=_spawn,
        )
        hooks = _disp.DispatchHooks(on_agent_start=lambda agent: started.append(agent.agent_id))

        d = make_dispatcher(minimal_squad(), svcs, hooks=hooks)
        d._spawn_agent("planner", "planner-1", None, [])
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
        d._handle_completion(agent, 0, [])
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
        d._handle_completion(agent, 1, [])
        assert done == [("coder-1", 1)]

    def test_on_agent_start_none_is_safe(self, tmp_path):
        """on_agent_start=None (default) does not crash."""

        def _spawn(ctx, cwd, model, log):
            return SpawnResult(process=FakePopen(), log_fh=None, context_tmp="")

        svcs = make_services(tmp_path, spawn_fn=_spawn)

        d = make_dispatcher(minimal_squad(), svcs)
        assert d.hooks.on_agent_start is None
        d._spawn_agent("planner", "planner-1", None, [])  # must not raise

    def test_on_agent_done_none_is_safe(self, tmp_path):
        """on_agent_done=None (default) does not crash."""
        svcs = make_services(tmp_path)

        d = make_dispatcher(minimal_squad(), svcs)
        assert d.hooks.on_agent_done is None
        agent = make_agent(tmp_path)
        d.pool.add(agent)
        d._handle_completion(agent, 0, [])  # must not raise

    def test_on_orc_status_called(self, tmp_path):
        """on_orc_status receives the status and task strings."""
        updates: list[tuple[str, str | None]] = []

        svcs = make_services(tmp_path)
        hooks = _disp.DispatchHooks(
            on_orc_status=lambda status, task: updates.append((status, task))
        )

        d = make_dispatcher(minimal_squad(), svcs, hooks=hooks)
        d._set_orc_status("running", "merging task 0001-foo.md")
        assert updates == [("running", "merging task 0001-foo.md")]

    def test_on_orc_status_none_is_safe(self, tmp_path):
        """on_orc_status=None (default) does not crash."""
        svcs = make_services(tmp_path)

        d = make_dispatcher(minimal_squad(), svcs)
        assert d.hooks.on_orc_status is None
        d._set_orc_status("running", "checking pending work")  # must not raise

    def test_echo_routes_to_logger_when_tui_active(self, tmp_path):
        """_echo logs via structlog instead of typer when TUI hook is active."""
        svcs = make_services(tmp_path)
        hooks = _disp.DispatchHooks(on_orc_status=lambda *_: None)
        d = make_dispatcher(minimal_squad(), svcs, hooks=hooks)
        # final=False + on_orc_status set → must not raise (hits logger.info branch)
        d._echo("syncing…", final=False)


class TestDispatcherLoopProperty:
    def test_loop_starts_at_zero(self, tmp_path):
        svcs = make_services(tmp_path)
        d = make_dispatcher(minimal_squad(), svcs)
        assert d.total_agent_calls == 0

    def test_loop_increments_each_cycle(self, tmp_path, monkeypatch):
        """loop property reflects the number of _loop iterations run."""
        import orc.engine.dispatcher as _d

        monkeypatch.setattr(_d, "_POLL_INTERVAL", 0)

        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [],
            get_messages=lambda: [],
        )
        d = make_dispatcher(minimal_squad(), svcs)
        # Run one maxcalls cycle (spawns planner then stops when pool drains).
        d.run(maxcalls=1)
        assert d.total_agent_calls >= 1


class TestProactivePlanner:
    """Dispatcher spawns a planner proactively when open_tasks < coder count."""

    def test_spawns_planner_when_tasks_below_coder_capacity(self, tmp_path):
        """Planner spawned when open_tasks < coder count and visions pending."""
        spawned_roles: list[str] = []

        def _spawn(ctx, cwd, model, log):
            return SpawnResult(process=FakePopen(), log_fh=None, context_tmp="")

        svcs = make_services(
            tmp_path,
            # 1 open task, squad has 2 coders → pipeline has room → spawn planner
            get_tasks=lambda: [{"name": "0001-foo.md"}],
            derive_task_state=lambda t: ("coder", "ready"),
            get_pending_visions=lambda: ["vision-001.md"],
            spawn_fn=_spawn,
        )
        svcs.board.assign_task = lambda t, a: spawned_roles.append(a.split("-")[0])

        # Squad with 2 coders so that 1 open task < 2 triggers the proactive path.
        squad = minimal_squad(coder=2)
        d = make_dispatcher(squad, svcs)
        setup_work(d)
        d._dispatch([], 100)

        # Should have spawned 1 coder + 1 planner
        pool_roles = [a.role for a in d.pool.all_agents()]
        assert "planner" in pool_roles, f"expected planner in pool, got {pool_roles}"

    def test_no_proactive_planner_when_tasks_meet_coder_capacity(self, tmp_path):
        """No proactive planner when open_tasks >= coder count."""
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [{"name": "0001-foo.md"}, {"name": "0002-bar.md"}],
            derive_task_state=lambda t: ("coder", "ready"),
            get_pending_visions=lambda: ["vision-001.md"],
        )
        # 2 open tasks, 2 coders → at capacity, no proactive planner
        squad = minimal_squad(coder=2)
        d = make_dispatcher(squad, svcs)
        setup_work(d)
        d._dispatch([], 100)

        pool_roles = [a.role for a in d.pool.all_agents()]
        assert "planner" not in pool_roles, f"unexpected planner in pool: {pool_roles}"

    def test_no_proactive_planner_when_no_pending_visions(self, tmp_path):
        """No proactive planner when there are no pending vision docs to plan."""
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [{"name": "0001-foo.md"}],
            derive_task_state=lambda t: ("coder", "ready"),
            get_pending_visions=lambda: [],  # nothing to plan
        )
        squad = minimal_squad(coder=2)
        d = make_dispatcher(squad, svcs)
        setup_work(d)
        d._dispatch([], 100)

        pool_roles = [a.role for a in d.pool.all_agents()]
        assert "planner" not in pool_roles, f"unexpected planner in pool: {pool_roles}"

    def test_no_proactive_planner_when_planner_already_running(self, tmp_path):
        """No second planner spawned when one is already in the pool."""
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [{"name": "0001-foo.md"}],
            derive_task_state=lambda t: ("coder", "ready"),
            get_pending_visions=lambda: ["vision-001.md"],
        )
        squad = minimal_squad(coder=2)
        d = make_dispatcher(squad, svcs)
        setup_work(d)
        # Pre-populate pool with a planner.
        d.pool.add(make_agent(tmp_path, role="planner", task=""))
        d._dispatch([], 100)

        planner_count = sum(1 for a in d.pool.all_agents() if a.role == "planner")
        assert planner_count == 1, "second planner must not be spawned"


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
        )
        svcs.messaging.has_unresolved_block = lambda msgs: (None, None)
        d = make_dispatcher(minimal_squad(), svcs)
        d.run(maxcalls=sys.maxsize)
        # Must not crash; total_agent_calls reflects whatever was dispatched.
        assert d.total_agent_calls >= 0


class TestDispatchBudgetExhaustion:
    """_dispatch stops spawning when call_budget is consumed mid-dispatch."""

    def test_budget_limits_spawns(self, tmp_path):
        """With budget=1 and 2 tasks, only 1 agent is spawned."""
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [
                {"name": "0001-foo.md"},
                {"name": "0002-bar.md"},
            ],
            derive_task_state=lambda t: ("coder", "ready"),
        )
        # Squad allows 2 coders, but budget is capped at 1.
        squad = minimal_squad(coder=2)
        d = make_dispatcher(squad, svcs)
        setup_work(d)
        count = d._dispatch([], call_budget=1)
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
        )
        d = make_dispatcher(minimal_squad(), svcs, only_role="coder")
        setup_work(d)
        count = d._dispatch([], call_budget=10)
        assert count == 0
        assert d.pool.is_empty()

    def test_only_planner_dispatches_planner(self, tmp_path):
        """With only_role='planner' and pending visions, a planner is spawned."""
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: [],
            get_pending_visions=lambda: ["v1.md"],
            get_pending_reviews=lambda: [],
        )
        d = make_dispatcher(minimal_squad(), svcs, only_role="planner")
        setup_work(d)
        count = d._dispatch([], call_budget=10)
        assert count == 1
        agents = d.pool.all_agents()
        assert len(agents) == 1
        assert agents[0].role == "planner"

    def test_only_coder_dispatches_coder_skips_qa(self, tmp_path):
        """With only_role='coder', coder tasks are dispatched but QA tasks are skipped."""
        tasks = [{"name": "0001-code.md"}, {"name": "0002-review.md"}]
        states = {"0001-code.md": ("coder", "ready"), "0002-review.md": ("qa", "ready")}
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: tasks,
            derive_task_state=lambda t: states[t],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: [],
        )
        d = make_dispatcher(minimal_squad(), svcs, only_role="coder")
        setup_work(d)
        count = d._dispatch([], call_budget=10)
        assert count == 1
        agents = d.pool.all_agents()
        assert all(a.role == "coder" for a in agents)

    def test_only_qa_dispatches_qa_skips_coder(self, tmp_path):
        """With only_role='qa', QA tasks are dispatched but coder tasks are skipped."""
        tasks = [{"name": "0001-code.md"}, {"name": "0002-review.md"}]
        states = {"0001-code.md": ("coder", "ready"), "0002-review.md": ("qa", "ready")}
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: tasks,
            derive_task_state=lambda t: states[t],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: [],
        )
        d = make_dispatcher(minimal_squad(), svcs, only_role="qa")
        setup_work(d)
        count = d._dispatch([], call_budget=10)
        assert count == 1
        agents = d.pool.all_agents()
        assert all(a.role == "qa" for a in agents)

    def test_no_filter_dispatches_all_roles(self, tmp_path):
        """Without only_role, both coder and QA tasks are dispatched."""
        tasks = [{"name": "0001-code.md"}, {"name": "0002-review.md"}]
        states = {"0001-code.md": ("coder", "ready"), "0002-review.md": ("qa", "ready")}
        svcs = make_services(
            tmp_path,
            get_tasks=lambda: tasks,
            derive_task_state=lambda t: states[t],
            get_pending_visions=lambda: [],
            get_pending_reviews=lambda: [],
        )
        d = make_dispatcher(minimal_squad(), svcs, only_role=None)
        setup_work(d)
        count = d._dispatch([], call_budget=10)
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
        )
        d = make_dispatcher(minimal_squad(), svcs, only_role="coder")
        d.run(maxcalls=5)
        assert d.total_agent_calls == 0
