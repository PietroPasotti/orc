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
        assert route(s) is None
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
    """Verify route() agrees with _derive_task_state (board-status based).

    Parametrises over (branch_exists, commits_ahead, merged_into_dev, board_status)
    and asserts the formal model and the live implementation return the same action.
    """

    def _patch_git(
        self,
        monkeypatch,
        *,
        branch_exists,
        commits_ahead,
        merged_into_dev,
        board_status=None,
    ):
        monkeypatch.setattr("orc.git.core._feature_branch_exists", lambda b: branch_exists)
        monkeypatch.setattr(
            "orc.git.core._feature_has_commits_ahead_of_main", lambda b: commits_ahead
        )
        monkeypatch.setattr("orc.git.core._feature_merged_into_dev", lambda b: merged_into_dev)

    _STATUS_TO_LAST_COMMIT = {
        "planned": LastCommit.CODER_WORK,
        "in-progress": LastCommit.CODER_WORK,
        "in-review": LastCommit.CODER_DONE,
        "done": LastCommit.QA_PASSED,
        "blocked": LastCommit.CODER_WORK,
    }

    @pytest.mark.parametrize(
        "branch_exists,commits_ahead,merged_into_dev,board_status",
        [
            (False, False, False, None),
            (False, False, True, None),
            (True, False, False, None),
            (True, False, True, None),
            (True, True, False, "in-progress"),
            (True, True, False, "in-review"),
            (True, True, False, "done"),
            (True, True, False, "blocked"),
        ],
    )
    def test_route_matches_derive_task_state(
        self,
        monkeypatch,
        branch_exists,
        commits_ahead,
        merged_into_dev,
        board_status,
    ):
        task_data = {"name": "0001-foo.md", "status": board_status} if board_status else None
        self._patch_git(
            monkeypatch,
            branch_exists=branch_exists,
            commits_ahead=commits_ahead,
            merged_into_dev=merged_into_dev,
        )
        impl_token, _ = _git._derive_task_state("0001-foo.md", task_data=task_data)

        last_commit = (
            self._STATUS_TO_LAST_COMMIT.get(board_status, LastCommit.CODER_WORK)
            if commits_ahead
            else LastCommit.NONE
        )
        world = WorldState(
            has_open_task=True,
            branch_exists=branch_exists,
            commits_ahead=commits_ahead,
            merged_into_dev=merged_into_dev,
            last_commit=last_commit,
        )
        model_action = route(world)

        assert model_action == impl_token, (
            f"route({world}) = {model_action!r} but _derive_task_state returned {impl_token!r}"
        )
