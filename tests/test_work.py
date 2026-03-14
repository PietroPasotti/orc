"""Tests for the Work snapshot dataclass (orc.engine.work)."""

from __future__ import annotations

import pytest

from orc.engine.work import Work

# ---------------------------------------------------------------------------
# any_work()
# ---------------------------------------------------------------------------


def test_any_work_empty_returns_false():
    w = Work(
        open_tasks=[],
        open_visions=[],
        open_todos_and_fixmes=[],
        open_PRs=[],
        stalled_agents=[],
    )
    assert w.any_work() is False


@pytest.mark.parametrize(
    "open_tasks,open_visions,open_todos_and_fixmes,open_PRs,stalled_agents",
    [
        ([{"name": "0001-foo.md"}], [], [], [], []),
        ([], ["0002-vision.md"], [], [], []),
        ([], [], [{"file": "src/foo.py", "line": 1, "tag": "TODO", "text": "fix me"}], [], []),
        ([], [], [], ["feat/0003-bar"], []),
    ],
)
def test_any_work_with_open_items(
    open_tasks, open_visions, open_todos_and_fixmes, open_PRs, stalled_agents
):
    w = Work(
        open_tasks=open_tasks,
        open_visions=open_visions,
        open_todos_and_fixmes=open_todos_and_fixmes,
        open_PRs=open_PRs,
        stalled_agents=stalled_agents,
    )
    assert w.any_work() is True


def test_any_work_stalled_agents():
    w = Work(
        open_tasks=[],
        open_visions=[],
        open_todos_and_fixmes=[],
        open_PRs=[],
        stalled_agents=[("coder-1", "blocked")],
    )
    assert w.any_work() is True


# ---------------------------------------------------------------------------
# has_planner_work
# ---------------------------------------------------------------------------


def test_has_planner_work_false_when_empty():
    w = Work(
        open_tasks=[],
        open_visions=[],
        open_todos_and_fixmes=[],
        open_PRs=[],
        stalled_agents=[],
    )
    assert w.has_planner_work is False


def test_has_planner_work_true_with_visions():
    w = Work(
        open_tasks=[{"name": "0001.md"}],  # tasks don't count for planner
        open_visions=["0002-new.md"],
        open_todos_and_fixmes=[],
        open_PRs=[],
        stalled_agents=[],
    )
    assert w.has_planner_work is True


def test_has_planner_work_true_with_todos():
    w = Work(
        open_tasks=[],
        open_visions=[],
        open_todos_and_fixmes=[{"file": "a.py", "line": 5, "tag": "FIXME", "text": ""}],
        open_PRs=[],
        stalled_agents=[],
    )
    assert w.has_planner_work is True


def test_has_planner_work_false_tasks_only():
    # Open tasks on the board don't count as planner work
    w = Work(
        open_tasks=[{"name": "0001.md"}],
        open_visions=[],
        open_todos_and_fixmes=[],
        open_PRs=[],
        stalled_agents=[],
    )
    assert w.has_planner_work is False


# ---------------------------------------------------------------------------
# hard_blocked / soft_blocked
# ---------------------------------------------------------------------------


def test_hard_blocked_none_when_empty():
    w = Work([], [], [], [], [])
    assert w.hard_blocked is None


def test_soft_blocked_none_when_empty():
    w = Work([], [], [], [], [])
    assert w.soft_blocked is None


def test_hard_blocked_returns_first_blocked_agent():
    w = Work([], [], [], [], [("coder-1", "soft-blocked"), ("coder-2", "blocked")])
    result = w.hard_blocked
    assert result == ("coder-2", "blocked")


def test_soft_blocked_returns_first_soft_blocked_agent():
    w = Work([], [], [], [], [("coder-1", "soft-blocked"), ("coder-2", "blocked")])
    result = w.soft_blocked
    assert result == ("coder-1", "soft-blocked")


def test_hard_blocked_none_when_only_soft():
    w = Work([], [], [], [], [("coder-1", "soft-blocked")])
    assert w.hard_blocked is None


def test_soft_blocked_none_when_only_hard():
    w = Work([], [], [], [], [("coder-1", "blocked")])
    assert w.soft_blocked is None


# ---------------------------------------------------------------------------
# Immutability (frozen dataclass)
# ---------------------------------------------------------------------------


def test_work_is_frozen():
    w = Work([], [], [], [], [])
    with pytest.raises((AttributeError, TypeError)):
        w.open_tasks = [{"name": "hack.md"}]  # type: ignore[misc]
