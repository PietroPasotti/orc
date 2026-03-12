"""Tests for orc/state_machine.py — WorldState formal model and WorkflowState enum."""

from __future__ import annotations

from collections import deque

import pytest

import orc.git.core as _git
from orc.dispatcher import CLOSE_BOARD, QA_PASSED
from orc.state_machine import (
    ACTION_CLOSE_BOARD,
    ACTION_QA_PASSED,
    BlockState,
    LastCommit,
    SystemState,
    TaskState,
    WorkflowState,
    WorkflowStateMachine,
    WorldState,
    _agent_to_state,
    _system_is_complete,
    is_complete,
    is_terminal,
    route,
    successors,
    system_route,
    system_successors,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_world_states() -> list[WorldState]:
    """Enumerate all valid WorldState combinations (respecting invariants)."""
    states = []
    for has_open_task in (True, False):
        for has_pending_vision in (True, False):
            for block in BlockState:
                if not has_open_task:
                    # No task — git fields are irrelevant; use neutral values.
                    states.append(
                        WorldState(
                            has_open_task=False,
                            has_pending_vision=has_pending_vision,
                            block=block,
                        )
                    )
                    continue
                for branch_exists in (True, False):
                    for merged_into_dev in (True, False):
                        # commits_ahead=True requires branch_exists=True
                        for commits_ahead in (True, False) if branch_exists else (False,):
                            for last_commit in LastCommit if commits_ahead else (LastCommit.NONE,):
                                states.append(
                                    WorldState(
                                        has_open_task=has_open_task,
                                        has_pending_vision=has_pending_vision,
                                        branch_exists=branch_exists,
                                        commits_ahead=commits_ahead,
                                        merged_into_dev=merged_into_dev,
                                        last_commit=last_commit,
                                        block=block,
                                    )
                                )
    return states


def _reachability_graph() -> tuple[set[WorldState], dict[WorldState, set[WorldState]]]:
    """BFS from all plausible entry states; return (visited, forward-edges)."""
    # Entry states: either a fresh vision waiting to be planned, or an
    # existing open task with a freshly-created (empty) feature branch.
    entry_states: list[WorldState] = [
        WorldState(has_open_task=False, has_pending_vision=True),
        WorldState(
            has_open_task=True,
            branch_exists=False,
            commits_ahead=False,
            merged_into_dev=False,
        ),
    ]
    visited: set[WorldState] = set()
    edges: dict[WorldState, set[WorldState]] = {}
    queue: deque[WorldState] = deque(entry_states)
    while queue:
        s = queue.popleft()
        if s in visited:
            continue
        visited.add(s)
        nexts = successors(s)
        edges[s] = set(nexts)
        for ns in nexts:
            if ns not in visited:
                queue.append(ns)
    return visited, edges


# ---------------------------------------------------------------------------
# Formal model — route() unit tests
# ---------------------------------------------------------------------------


class TestRoute:
    def test_hard_block_stops(self):
        s = WorldState(has_open_task=True, block=BlockState.HARD)
        assert route(s) is None

    def test_soft_block_routes_to_planner(self):
        s = WorldState(has_open_task=True, block=BlockState.SOFT)
        assert route(s) == "planner"

    def test_no_task_no_vision_is_complete(self):
        s = WorldState(has_open_task=False, has_pending_vision=False)
        assert is_complete(s)

    def test_no_task_with_vision_routes_to_planner(self):
        s = WorldState(has_open_task=False, has_pending_vision=True)
        assert route(s) == "planner"

    def test_open_task_no_branch_routes_to_coder(self):
        """When branch doesn't exist, always dispatch coder regardless of merged state."""
        s = WorldState(has_open_task=True, branch_exists=False, merged_into_dev=False)
        assert route(s) == "coder"

    def test_open_task_no_branch_even_if_merged_routes_to_coder(self):
        """Branch absent — cannot safely check merge ancestry; dispatch coder."""
        s = WorldState(has_open_task=True, branch_exists=False, merged_into_dev=True)
        assert route(s) == "coder"

    def test_branch_exists_no_commits_not_merged_routes_to_coder(self):
        s = WorldState(
            has_open_task=True,
            branch_exists=True,
            commits_ahead=False,
            merged_into_dev=False,
        )
        assert route(s) == "coder"

    def test_branch_exists_no_commits_already_merged_closes_board(self):
        """Regression: branch left behind after merge must not re-dispatch a coder."""
        s = WorldState(
            has_open_task=True,
            branch_exists=True,
            commits_ahead=False,
            merged_into_dev=True,
        )
        assert route(s) == ACTION_CLOSE_BOARD

    def test_coder_commits_routes_to_coder(self):
        """CODER_WORK routes back to coder — they're still working."""
        s = WorldState(
            has_open_task=True,
            branch_exists=True,
            commits_ahead=True,
            last_commit=LastCommit.CODER_WORK,
        )
        assert route(s) == "coder"

    def test_qa_passed_routes_to_merge(self):
        s = WorldState(
            has_open_task=True,
            branch_exists=True,
            commits_ahead=True,
            last_commit=LastCommit.QA_PASSED,
        )
        assert route(s) == ACTION_QA_PASSED

    def test_qa_other_routes_back_to_coder(self):
        s = WorldState(
            has_open_task=True,
            branch_exists=True,
            commits_ahead=True,
            last_commit=LastCommit.QA_OTHER,
        )
        assert route(s) == "coder"

    def test_coder_done_routes_to_qa(self):
        """CODER_DONE is a richer variant of CODER_WORK — still routes to QA."""
        s = WorldState(
            has_open_task=True,
            branch_exists=True,
            commits_ahead=True,
            last_commit=LastCommit.CODER_DONE,
        )
        assert route(s) == "qa"


# ---------------------------------------------------------------------------
# Formal model — sentinel alignment with dispatcher constants
# ---------------------------------------------------------------------------


class TestSentinelAlignment:
    """Verify that the formal model's sentinels match the dispatcher's."""

    def test_close_board_sentinel(self):
        assert ACTION_CLOSE_BOARD == CLOSE_BOARD

    def test_qa_passed_sentinel(self):
        assert ACTION_QA_PASSED == QA_PASSED


# ---------------------------------------------------------------------------
# Formal model — cross-check against _derive_task_state implementation
# ---------------------------------------------------------------------------


class TestRouteMatchesImplementation:
    """Verify route() agrees with the real git.py implementation.

    These tests parametrise over key (branch_exists, commits_ahead,
    merged_into_dev, last_commit) combinations and assert that the formal
    model and the live _derive_task_state return the same action.
    """

    def _patch_git(
        self,
        monkeypatch,
        *,
        branch_exists,
        commits_ahead,
        merged_into_dev,
        last_commit_msg=None,
    ):
        monkeypatch.setattr("orc.git.core._feature_branch_exists", lambda b: branch_exists)
        monkeypatch.setattr(
            "orc.git.core._feature_has_commits_ahead_of_main", lambda b: commits_ahead
        )
        monkeypatch.setattr("orc.git.core._feature_merged_into_dev", lambda b: merged_into_dev)
        monkeypatch.setattr("orc.git.core._last_feature_commit_message", lambda b: last_commit_msg)
        monkeypatch.setattr("orc.board._active_task_name", lambda: "0001-foo.md")

    def _last_commit_from_msg(self, msg: str | None) -> LastCommit:
        if msg is None:
            return LastCommit.NONE
        # New structured exit-commit format.
        from orc.git.core import _parse_exit_scope

        parsed = _parse_exit_scope(msg)
        if parsed is not None:
            _agent_id, action, _task_code = parsed
            if action == "approve":
                return LastCommit.QA_PASSED
            if action == "reject":
                return LastCommit.QA_OTHER
            if action == "done":
                return LastCommit.CODER_DONE
        # Legacy format.
        if msg.startswith("qa(passed)"):
            return LastCommit.QA_PASSED
        if msg.startswith("qa("):
            return LastCommit.QA_OTHER
        return LastCommit.CODER_WORK

    @pytest.mark.parametrize(
        "branch_exists,commits_ahead,merged_into_dev,last_commit_msg",
        [
            (False, False, False, None),
            (False, False, True, None),
            (True, False, False, None),
            (True, False, True, None),
            (True, True, False, "feat: add thing"),
            (True, True, False, "qa(passed): all good"),
            (True, True, False, "qa(failed): coverage low"),
            # New structured exit-commit format.
            (True, True, False, "chore(coder-1.done.0001): implementation complete"),
            (True, True, False, "chore(qa-1.approve.0001): all checks green"),
            (True, True, False, "chore(qa-2.reject.0001): missing error-path tests"),
        ],
    )
    def test_route_matches_derive_task_state(
        self,
        monkeypatch,
        branch_exists,
        commits_ahead,
        merged_into_dev,
        last_commit_msg,
    ):
        self._patch_git(
            monkeypatch,
            branch_exists=branch_exists,
            commits_ahead=commits_ahead,
            merged_into_dev=merged_into_dev,
            last_commit_msg=last_commit_msg,
        )
        impl_token, _ = _git._derive_task_state("0001-foo.md")

        last_commit = self._last_commit_from_msg(last_commit_msg)
        world = WorldState(
            has_open_task=True,
            branch_exists=branch_exists,
            commits_ahead=commits_ahead,
            merged_into_dev=merged_into_dev,
            last_commit=last_commit if commits_ahead else LastCommit.NONE,
        )
        model_action = route(world)

        assert model_action == impl_token, (
            f"route({world}) = {model_action!r} but _derive_task_state returned {impl_token!r}"
        )


# ---------------------------------------------------------------------------
# Formal model — property tests (deadlock freedom, liveness)
# ---------------------------------------------------------------------------


class TestDeadlockFreedom:
    """Exhaustive graph-reachability checks over the full WorldState space."""

    def test_no_deadlocks(self):
        """Every non-hard-blocked reachable state can eventually reach COMPLETE.

        Algorithm
        ---------
        1. BFS from plausible entry states to collect all reachable states.
        2. Build the reverse adjacency graph.
        3. BFS backward from COMPLETE terminal states.
        4. Assert that every non-hard-blocked state was reached in step 3.
        """
        visited, forward = _reachability_graph()

        complete_states = {s for s in visited if is_complete(s)}
        assert complete_states, "No COMPLETE terminal states reachable at all!"

        # Build reverse graph.
        reverse: dict[WorldState, set[WorldState]] = {s: set() for s in visited}
        for s, nexts in forward.items():
            for ns in nexts:
                if ns in reverse:
                    reverse[ns].add(s)

        # BFS backward from COMPLETE.
        can_reach_complete: set[WorldState] = set(complete_states)
        queue: deque[WorldState] = deque(complete_states)
        while queue:
            s = queue.popleft()
            for pred in reverse.get(s, set()):
                if pred not in can_reach_complete:
                    can_reach_complete.add(pred)
                    queue.append(pred)

        non_blocked = {s for s in visited if s.block != BlockState.HARD}
        deadlocks = non_blocked - can_reach_complete
        assert not deadlocks, f"{len(deadlocks)} deadlock state(s) found:\n" + "\n".join(
            f"  route={route(s)!r}  state={s}" for s in sorted(deadlocks, key=str)
        )

    def test_hard_block_has_no_successors(self):
        """A hard-blocked state is terminal — no agent is spawned."""
        s = WorldState(has_open_task=True, block=BlockState.HARD)
        assert successors(s) == frozenset()

    def test_complete_has_no_successors(self):
        """The COMPLETE terminal state is genuinely terminal."""
        s = WorldState(has_open_task=False, has_pending_vision=False)
        assert is_complete(s)
        assert successors(s) == frozenset()

    def test_every_non_terminal_has_successors(self):
        """Every reachable non-terminal state produces at least one successor."""
        visited, _ = _reachability_graph()
        for s in visited:
            if not is_terminal(s):
                assert successors(s), f"Non-terminal state has no successors: {s}"

    def test_all_world_states_route_without_error(self):
        """route() and successors() must never raise for any valid WorldState."""
        for s in _all_world_states():
            route(s)
            successors(s)


# ---------------------------------------------------------------------------
# TaskState
# ---------------------------------------------------------------------------


class TestTaskState:
    def test_task_state_defaults(self):
        ts = TaskState()
        assert ts.branch_exists is False
        assert ts.commits_ahead is False
        assert ts.merged_into_dev is False
        assert ts.last_commit == LastCommit.NONE

    def test_world_state_task_state_roundtrip(self):
        w = WorldState(
            has_open_task=True,
            branch_exists=True,
            commits_ahead=True,
            merged_into_dev=False,
            last_commit=LastCommit.CODER_WORK,
        )
        ts = w.task_state()
        assert ts == TaskState(
            branch_exists=True,
            commits_ahead=True,
            merged_into_dev=False,
            last_commit=LastCommit.CODER_WORK,
        )

    def test_task_state_is_frozen(self):
        ts = TaskState()
        with pytest.raises((AttributeError, TypeError)):
            ts.branch_exists = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Coarse enum / WorkflowStateMachine
# ---------------------------------------------------------------------------


class TestWorkflowStateEnum:
    def test_all_expected_states_exist(self):
        names = {s.value for s in WorkflowState}
        assert "idle" in names
        assert "planning" in names
        assert "coding" in names
        assert "reviewing" in names
        assert "merging" in names
        assert "blocked" in names
        assert "soft_blocked" in names
        assert "complete" in names

    def test_enum_values_are_strings(self):
        for state in WorkflowState:
            assert isinstance(state.value, str)


class TestAgentToState:
    def test_none_maps_to_idle(self):
        assert _agent_to_state(None) == WorkflowState.IDLE

    def test_planner_maps_to_planning(self):
        assert _agent_to_state("planner") == WorkflowState.PLANNING

    def test_coder_maps_to_coding(self):
        assert _agent_to_state("coder") == WorkflowState.CODING

    def test_qa_maps_to_reviewing(self):
        assert _agent_to_state("qa") == WorkflowState.REVIEWING

    def test_unknown_maps_to_idle(self):
        assert _agent_to_state("unknown_agent") == WorkflowState.IDLE


class TestWorkflowStateMachine:
    def _make_machine(self, next_agent):
        """Return a state machine whose determine_next_agent always returns *next_agent*."""
        return WorkflowStateMachine(determine_next_agent_fn=lambda msgs: (next_agent, "test"))

    def test_current_state_coding(self):
        m = self._make_machine("coder")
        assert m.current_state([]) == WorkflowState.CODING

    def test_current_state_reviewing(self):
        m = self._make_machine("qa")
        assert m.current_state([]) == WorkflowState.REVIEWING

    def test_current_state_planning(self):
        m = self._make_machine("planner")
        assert m.current_state([]) == WorkflowState.PLANNING

    def test_current_state_idle_when_none(self):
        m = self._make_machine(None)
        assert m.current_state([]) == WorkflowState.IDLE

    def test_next_agent_returns_value(self):
        m = self._make_machine("coder")
        assert m.next_agent([]) == "coder"

    def test_next_agent_returns_none(self):
        m = self._make_machine(None)
        assert m.next_agent([]) is None

    def test_passes_messages_to_fn(self):
        received = {}

        def fn(messages):
            received["messages"] = messages
            return None, "done"

        m = WorkflowStateMachine(determine_next_agent_fn=fn)
        msgs = [{"text": "hi"}]
        m.current_state(msgs)
        assert received["messages"] is msgs


# ---------------------------------------------------------------------------
# System-level model — SystemState, system_route(), system_successors()
# ---------------------------------------------------------------------------


def _system_reachability_graph(
    entry_states: frozenset[SystemState],
) -> tuple[set[SystemState], dict[SystemState, set[SystemState]]]:
    """BFS over the system state space from *entry_states*.

    Returns (visited, forward_edges).
    """
    visited: set[SystemState] = set()
    forward: dict[SystemState, set[SystemState]] = {}
    queue: deque[SystemState] = deque(entry_states)
    visited.update(entry_states)

    while queue:
        s = queue.popleft()
        nexts = system_successors(s)
        forward[s] = set(nexts)
        for ns in nexts:
            if ns not in visited:
                visited.add(ns)
                queue.append(ns)

    return visited, forward


class TestSystemState:
    def test_defaults(self):
        s = SystemState()
        assert s.tasks == frozenset()
        assert s.pending_visions == 0
        assert s.block == BlockState.NONE

    def test_pending_visions_capped_at_2(self):
        s = SystemState(pending_visions=99)
        assert s.pending_visions == 2

    def test_is_frozen(self):
        s = SystemState()
        with pytest.raises((AttributeError, TypeError)):
            s.pending_visions = 5  # type: ignore[misc]

    def test_complete_when_no_tasks_no_visions(self):
        assert _system_is_complete(SystemState())

    def test_not_complete_with_pending_vision(self):
        assert not _system_is_complete(SystemState(pending_visions=1))

    def test_not_complete_with_open_task(self):
        assert not _system_is_complete(SystemState(tasks=frozenset({TaskState()})))

    def test_not_complete_when_hard_blocked(self):
        assert not _system_is_complete(SystemState(block=BlockState.HARD))


class TestSystemRoute:
    def test_hard_block_returns_none(self):
        s = SystemState(tasks=frozenset({TaskState()}), block=BlockState.HARD)
        assert system_route(s) is None

    def test_soft_block_returns_planner_key(self):
        s = SystemState(tasks=frozenset({TaskState()}), block=BlockState.SOFT)
        actions = system_route(s)
        assert actions is not None
        assert "planner" in actions.values()

    def test_complete_returns_empty_dict(self):
        s = SystemState()
        assert system_route(s) == {}

    def test_vision_routes_to_planner(self):
        s = SystemState(pending_visions=1)
        actions = system_route(s)
        assert actions is not None
        assert "planner" in actions.values()

    def test_task_with_no_branch_routes_to_coder(self):
        task = TaskState(branch_exists=False)
        s = SystemState(tasks=frozenset({task}))
        actions = system_route(s)
        assert actions is not None
        assert actions[task] == "coder"

    def test_task_with_coder_commits_routes_to_coder(self):
        task = TaskState(branch_exists=True, commits_ahead=True, last_commit=LastCommit.CODER_WORK)
        s = SystemState(tasks=frozenset({task}))
        actions = system_route(s)
        assert actions is not None
        assert actions[task] == "coder"

    def test_two_tasks_both_dispatched(self):
        t1 = TaskState(branch_exists=False)
        t2 = TaskState(branch_exists=True, commits_ahead=True, last_commit=LastCommit.CODER_DONE)
        s = SystemState(tasks=frozenset({t1, t2}))
        actions = system_route(s)
        assert actions is not None
        assert actions[t1] == "coder"
        assert actions[t2] == "qa"


class TestSystemSuccessors:
    def test_hard_blocked_has_no_successors(self):
        s = SystemState(tasks=frozenset({TaskState()}), block=BlockState.HARD)
        assert system_successors(s) == frozenset()

    def test_complete_has_no_successors(self):
        s = SystemState()
        assert system_successors(s) == frozenset()

    def test_soft_block_resolved_by_planner(self):
        task = TaskState()
        s = SystemState(tasks=frozenset({task}), block=BlockState.SOFT)
        nexts = system_successors(s)
        assert any(ns.block == BlockState.NONE for ns in nexts)

    def test_coder_success_adds_commits(self):
        task = TaskState(branch_exists=False)
        s = SystemState(tasks=frozenset({task}))
        nexts = system_successors(s)
        expected_done = TaskState(
            branch_exists=True,
            commits_ahead=True,
            last_commit=LastCommit.CODER_WORK,
        )
        assert any(expected_done in ns.tasks for ns in nexts)

    def test_coder_can_hard_block(self):
        task = TaskState(branch_exists=False)
        s = SystemState(tasks=frozenset({task}))
        nexts = system_successors(s)
        assert any(ns.block == BlockState.HARD for ns in nexts)

    def test_qa_passed_removes_task(self):
        task = TaskState(branch_exists=True, commits_ahead=True, last_commit=LastCommit.QA_PASSED)
        s = SystemState(tasks=frozenset({task}))
        nexts = system_successors(s)
        assert any(task not in ns.tasks for ns in nexts)


@pytest.mark.parametrize(
    "entry_tasks",
    [
        # Single task — no branch yet
        [TaskState()],
        # Two tasks in different git states
        [
            TaskState(),
            TaskState(branch_exists=True, commits_ahead=True, last_commit=LastCommit.CODER_WORK),
        ],
        # Three tasks: new, in-progress, ready-for-qa
        [
            TaskState(),
            TaskState(branch_exists=True, commits_ahead=True, last_commit=LastCommit.CODER_WORK),
            TaskState(branch_exists=True, commits_ahead=True, last_commit=LastCommit.QA_PASSED),
        ],
        # Four tasks with mixed states
        [
            TaskState(),
            TaskState(branch_exists=True, commits_ahead=False),
            TaskState(branch_exists=True, commits_ahead=True, last_commit=LastCommit.CODER_WORK),
            TaskState(branch_exists=True, commits_ahead=True, last_commit=LastCommit.QA_OTHER),
        ],
    ],
    ids=["1-task", "2-tasks", "3-tasks", "4-tasks"],
)
def test_system_no_deadlocks(entry_tasks: list[TaskState]):
    """Every non-hard-blocked system state can eventually reach COMPLETE.

    Uses interleaving BFS (one agent completion per step).  The frozenset
    representation deduplicates structurally identical task states, keeping
    the state space tractable (BFS completes in milliseconds).
    """
    entry: frozenset[SystemState] = frozenset(
        {SystemState(tasks=frozenset(entry_tasks), pending_visions=v) for v in range(3)}
    )

    visited, forward = _system_reachability_graph(entry)

    complete_states = {s for s in visited if _system_is_complete(s)}
    assert complete_states, "No COMPLETE terminal states reachable!"

    reverse: dict[SystemState, set[SystemState]] = {s: set() for s in visited}
    for s, nexts in forward.items():
        for ns in nexts:
            if ns in reverse:
                reverse[ns].add(s)

    can_reach_complete: set[SystemState] = set(complete_states)
    queue: deque[SystemState] = deque(complete_states)
    while queue:
        s = queue.popleft()
        for pred in reverse.get(s, set()):
            if pred not in can_reach_complete:
                can_reach_complete.add(pred)
                queue.append(pred)

    non_blocked = {s for s in visited if s.block != BlockState.HARD}
    deadlocks = non_blocked - can_reach_complete
    assert not deadlocks, (
        f"[{len(entry_tasks)} task(s)] {len(deadlocks)} system deadlock(s) found:\n"
        + "\n".join(f"  route={system_route(s)!r}  state={s}" for s in list(deadlocks)[:5])
    )


class TestSystemCrossChecks:
    """Verify system_route() agrees with single-task route() for each task independently."""

    @pytest.mark.parametrize(
        "task",
        [
            TaskState(),
            TaskState(branch_exists=True, commits_ahead=False, merged_into_dev=False),
            TaskState(branch_exists=True, commits_ahead=False, merged_into_dev=True),
            TaskState(branch_exists=True, commits_ahead=True, last_commit=LastCommit.CODER_WORK),
            TaskState(branch_exists=True, commits_ahead=True, last_commit=LastCommit.QA_PASSED),
            TaskState(branch_exists=True, commits_ahead=True, last_commit=LastCommit.QA_OTHER),
        ],
    )
    def test_system_route_agrees_with_single_task_route(self, task: TaskState):
        """system_route() must agree with route() for each individual task."""
        w = WorldState(
            has_open_task=True,
            branch_exists=task.branch_exists,
            commits_ahead=task.commits_ahead,
            merged_into_dev=task.merged_into_dev,
            last_commit=task.last_commit,
        )
        expected = route(w)

        s = SystemState(tasks=frozenset({task}))
        actions = system_route(s)
        assert actions is not None
        assert actions.get(task) == expected, (
            f"system_route disagrees for task={task}: "
            f"system_route={actions.get(task)!r}, route={expected!r}"
        )
