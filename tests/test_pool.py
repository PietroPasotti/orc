"""Tests for orc/pool.py."""

from pathlib import Path
from unittest.mock import MagicMock

from conftest import FakePopen

from orc.engine.pool import AgentPool, AgentProcess


def _make_agent(tmp_path: Path, *, role: str = "coder", task: str = "0001-foo.md") -> AgentProcess:
    return AgentProcess(
        agent_id=f"{role}-1",
        role=role,
        model="copilot",
        task_name=task,
        process=FakePopen(),
        worktree=tmp_path,
        log_path=tmp_path / f"{role}.log",
        log_fh=None,
        context_tmp=None,
    )


class TestPoolCoverage:
    def test_get_returns_agent(self, tmp_path):
        pool = AgentPool()
        a = _make_agent(tmp_path)
        pool.add(a)
        assert pool.get("coder-1") is a

    def test_get_missing_returns_none(self):
        assert AgentPool().get("nope") is None

    def test_all_agents(self, tmp_path):
        pool = AgentPool()
        a = _make_agent(tmp_path)
        pool.add(a)
        assert pool.all_agents() == [a]

    def test_count_by_role(self, tmp_path):
        pool = AgentPool()
        pool.add(_make_agent(tmp_path, role="coder"))
        assert pool.count_by_role("coder") == 1
        assert pool.count_by_role("qa") == 0

    def test_running_by_role(self, tmp_path):
        """Line 103: running_by_role returns matching agents."""
        pool = AgentPool()
        pool.add(_make_agent(tmp_path, role="coder"))
        pool.add(
            AgentProcess(
                agent_id="qa-1",
                role="qa",
                model="copilot",
                task_name="t2",
                process=FakePopen(),
                worktree=tmp_path,
                log_path=tmp_path / "qa.log",
                log_fh=None,
                context_tmp=None,
            )
        )
        assert len(pool.running_by_role("coder")) == 1
        assert pool.running_by_role("planner") == []

    def test_kill_nonexistent_is_noop(self):
        AgentPool().kill("nope")

    def test_kill_exception_swallowed(self, tmp_path):
        """Lines 140-141: process.kill() raises → exception caught."""
        bad_proc = MagicMock()
        bad_proc.kill.side_effect = OSError("dead")
        bad_proc.wait.return_value = 0
        pool = AgentPool()
        pool.add(
            AgentProcess(
                agent_id="coder-1",
                role="coder",
                model="copilot",
                task_name="t",
                process=bad_proc,
                worktree=tmp_path,
                log_path=tmp_path / "log",
                log_fh=None,
                context_tmp=None,
            )
        )
        pool.kill("coder-1")  # must not raise

    def test_kill_all(self, tmp_path):
        pool = AgentPool()
        pool.add(_make_agent(tmp_path, role="coder"))
        pool.kill_all()
        assert pool.is_empty()

    def test_close_log_with_open_handle(self, tmp_path):
        log_file = tmp_path / "log.txt"
        fh = log_file.open("w")
        agent = AgentProcess(
            agent_id="coder-1",
            role="coder",
            model="copilot",
            task_name="t",
            process=FakePopen(),
            worktree=tmp_path,
            log_path=log_file,
            log_fh=fh,
            context_tmp=None,
        )
        pool = AgentPool()
        pool.close_log(agent)
        assert agent.log_fh is None

    def test_close_log_with_failing_handle(self, tmp_path):
        bad_fh = MagicMock()
        bad_fh.close.side_effect = OSError("closed")
        pool = AgentPool()
        agent = AgentProcess(
            agent_id="coder-1",
            role="coder",
            model="copilot",
            task_name="t",
            process=FakePopen(),
            worktree=tmp_path,
            log_path=tmp_path / "log",
            log_fh=bad_fh,
            context_tmp=None,
        )
        pool.close_log(agent)
        assert agent.log_fh is None
