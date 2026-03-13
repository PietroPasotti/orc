"""Tests for orc/state_machine.py — WorldState formal model."""

from __future__ import annotations

import pytest

import orc.git.core as _git
from orc.engine.dispatcher import CLOSE_BOARD, QA_PASSED
from orc.engine.state_machine import (
    ACTION_CLOSE_BOARD,
    ACTION_QA_PASSED,
    BlockState,
    LastCommit,
    WorldState,
    is_terminal,
    route,
)

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

    def test_no_task_no_vision_is_terminal(self):
        s = WorldState(has_open_task=False, has_pending_vision=False)
        assert is_terminal(s)
        assert s.block != BlockState.HARD

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
        return LastCommit.CODER_WORK

    @pytest.mark.parametrize(
        "branch_exists,commits_ahead,merged_into_dev,last_commit_msg",
        [
            (False, False, False, None),
            (False, False, True, None),
            (True, False, False, None),
            (True, False, True, None),
            (True, True, False, "feat: add thing"),
            # Structured exit-commit format.
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
