"""Tests for ConflictResolver (now in orc.engine.workflow)."""

from __future__ import annotations

from dataclasses import replace as _replace
from unittest.mock import patch

import pytest

import orc.config as _cfg
import orc.engine.context as _ctx
from orc.engine.workflow import ConflictResolutionFailed, ConflictResolver

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_resolver():
    """Return a ConflictResolver with a minimal stub SquadConfig."""

    class FakeSquad:
        def model(self, role: str) -> str:
            return "test-model"

    return ConflictResolver(squad_cfg=FakeSquad())


# ---------------------------------------------------------------------------
# resolve_merge_conflict
# ---------------------------------------------------------------------------


class TestResolveMergeConflict:
    def test_success(self, tmp_path, monkeypatch):
        """Coder resolves conflict, merge completes, no exception raised."""
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), work_dev_branch="dev"))
        resolver = _make_resolver()

        with (
            patch.object(_ctx, "build_agent_context", return_value=("system", "user")),
            patch("orc.ai.invoke.invoke", return_value=0),
            patch("orc.git.Git.is_merge_in_progress", return_value=False),
        ):
            # Should NOT raise
            resolver.resolve_merge_conflict("feat/task", tmp_path, "M src/foo.py")

    def test_coder_nonzero_raises_exit(self, tmp_path, monkeypatch):
        """Coder exits non-zero → ConflictResolutionFailed raised."""
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), work_dev_branch="dev"))
        resolver = _make_resolver()

        with (
            patch.object(_ctx, "build_agent_context", return_value=("system", "user")),
            patch("orc.ai.invoke.invoke", return_value=1),
        ):
            with pytest.raises(ConflictResolutionFailed):
                resolver.resolve_merge_conflict("feat/task", tmp_path, "M src/foo.py")

    def test_merge_still_in_progress_raises_exit(self, tmp_path, monkeypatch):
        """Coder succeeds but merge still in progress → aborts merge and raises."""
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), work_dev_branch="dev"))
        resolver = _make_resolver()
        aborted = []

        with (
            patch.object(_ctx, "build_agent_context", return_value=("system", "user")),
            patch("orc.ai.invoke.invoke", return_value=0),
            patch("orc.git.Git.is_merge_in_progress", return_value=True),
            patch("orc.git.Git.merge_abort", side_effect=lambda: aborted.append(True)),
        ):
            with pytest.raises(ConflictResolutionFailed):
                resolver.resolve_merge_conflict("feat/task", tmp_path, "M src/foo.py")
        assert aborted, "merge_abort should have been called"

    def test_coder_nonzero_aborts_stuck_merge(self, tmp_path, monkeypatch):
        """Coder exits non-zero with merge in progress → aborts merge before raising."""
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), work_dev_branch="dev"))
        resolver = _make_resolver()
        aborted = []

        with (
            patch.object(_ctx, "build_agent_context", return_value=("system", "user")),
            patch("orc.ai.invoke.invoke", return_value=1),
            patch("orc.git.Git.is_merge_in_progress", return_value=True),
            patch("orc.git.Git.merge_abort", side_effect=lambda: aborted.append(True)),
        ):
            with pytest.raises(ConflictResolutionFailed):
                resolver.resolve_merge_conflict("feat/task", tmp_path, "M src/foo.py")
        assert aborted, "merge_abort should have been called"

    def test_coder_nonzero_no_abort_when_no_merge_in_progress(self, tmp_path, monkeypatch):
        """Coder exits non-zero without merge in progress → no abort called."""
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), work_dev_branch="dev"))
        resolver = _make_resolver()
        aborted = []

        with (
            patch.object(_ctx, "build_agent_context", return_value=("system", "user")),
            patch("orc.ai.invoke.invoke", return_value=1),
            patch("orc.git.Git.is_merge_in_progress", return_value=False),
            patch("orc.git.Git.merge_abort", side_effect=lambda: aborted.append(True)),
        ):
            with pytest.raises(ConflictResolutionFailed):
                resolver.resolve_merge_conflict("feat/task", tmp_path, "M src/foo.py")
        assert not aborted, "merge_abort should NOT have been called"


# ---------------------------------------------------------------------------
# resolve_rebase_conflict
# ---------------------------------------------------------------------------


class TestResolveRebaseConflict:
    def test_coder_nonzero_aborts_stuck_rebase(self, tmp_path, monkeypatch):
        """Coder exits non-zero with rebase in progress → aborts rebase before raising."""
        monkeypatch.setattr(
            _cfg, "_config", _replace(_cfg.get(), work_dev_branch="dev", main_branch="main")
        )
        resolver = _make_resolver()
        aborted = []

        with (
            patch.object(_ctx, "build_agent_context", return_value=("system", "user")),
            patch("orc.ai.invoke.invoke", return_value=1),
            patch("orc.git.Git.is_rebase_in_progress", return_value=True),
            patch("orc.git.Git.rebase_abort", side_effect=lambda: aborted.append(True)),
        ):
            with pytest.raises(ConflictResolutionFailed):
                resolver.resolve_rebase_conflict(tmp_path, "UU src/bar.py")
        assert aborted, "rebase_abort should have been called"
