"""Tests for orc/state_machine.py — WorldState formal model and WorkflowState enum."""

from __future__ import annotations

from collections import deque

import pytest

import orc.git as _git
from orc.dispatcher import CLOSE_BOARD, QA_PASSED
from orc.state_machine import (
    ACTION_CLOSE_BOARD,
    ACTION_QA_PASSED,
    TRANSITIONS,
    BlockState,
    LastCommit,
    WorkflowState,
    WorkflowStateMachine,
    WorldState,
    _agent_to_state,
    is_complete,
    is_terminal,
    route,
    successors,
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
        s = WorldState(has_open_task=True, branch_exists=False, merged_into_dev=False)
        assert route(s) == "coder"

    def test_open_task_no_branch_but_merged_closes_board(self):
        s = WorldState(has_open_task=True, branch_exists=False, merged_into_dev=True)
        assert route(s) == ACTION_CLOSE_BOARD

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

    def test_coder_commits_routes_to_qa(self):
        s = WorldState(
            has_open_task=True,
            branch_exists=True,
            commits_ahead=True,
            last_commit=LastCommit.CODER_WORK,
        )
        assert route(s) == "qa"

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
        monkeypatch.setattr("orc.git._feature_branch_exists", lambda b: branch_exists)
        monkeypatch.setattr("orc.git._feature_has_commits_ahead_of_main", lambda b: commits_ahead)
        monkeypatch.setattr("orc.git._feature_merged_into_dev", lambda b: merged_into_dev)
        monkeypatch.setattr("orc.git._last_feature_commit_message", lambda b: last_commit_msg)
        monkeypatch.setattr("orc.board._active_task_name", lambda: "0001-foo.md")

    def _last_commit_from_msg(self, msg: str | None) -> LastCommit:
        if msg is None:
            return LastCommit.NONE
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
# Coarse enum / WorkflowStateMachine (existing tests, unchanged)
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


class TestTransitionTable:
    def test_all_states_have_transitions(self):
        for state in WorkflowState:
            assert state in TRANSITIONS, f"No transition entry for {state}"

    def test_targets_are_valid_states(self):
        for state, targets in TRANSITIONS.items():
            for target in targets:
                if target is not None:
                    assert isinstance(target, WorkflowState)


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
        return WorkflowStateMachine(determine_next_agent_fn=lambda *a, **kw: next_agent)

    def test_current_state_coding(self):
        m = self._make_machine("coder")
        assert m.current_state({}, []) == WorkflowState.CODING

    def test_current_state_reviewing(self):
        m = self._make_machine("qa")
        assert m.current_state({}, []) == WorkflowState.REVIEWING

    def test_current_state_planning(self):
        m = self._make_machine("planner")
        assert m.current_state({}, []) == WorkflowState.PLANNING

    def test_current_state_idle_when_none(self):
        m = self._make_machine(None)
        assert m.current_state({}, []) == WorkflowState.IDLE

    def test_next_agent_returns_value(self):
        m = self._make_machine("coder")
        assert m.next_agent({}, []) == "coder"

    def test_next_agent_returns_none(self):
        m = self._make_machine(None)
        assert m.next_agent({}, []) is None

    def test_passes_task_and_messages_to_fn(self):
        received = {}

        def fn(task, messages, worktree=None):
            received["task"] = task
            received["messages"] = messages
            received["worktree"] = worktree
            return None

        m = WorkflowStateMachine(determine_next_agent_fn=fn)
        task = {"name": "test"}
        msgs = [{"text": "hi"}]
        m.current_state(task, msgs, worktree=None)
        assert received["task"] is task
        assert received["messages"] is msgs
