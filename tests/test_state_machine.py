"""Tests for orc/state_machine.py — WorkflowState enum and WorkflowStateMachine."""

from __future__ import annotations

from orc.state_machine import (
    TRANSITIONS,
    WorkflowState,
    WorkflowStateMachine,
    _agent_to_state,
)


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
