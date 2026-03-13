"""Tests for orc/conflict.py — ConflictResolver."""

from __future__ import annotations

from dataclasses import replace as _replace
from unittest.mock import patch

import pytest

import orc.config as _cfg
import orc.engine.context as _ctx
import orc.git.core as _git
from orc.git.conflict import ConflictResolutionFailed, ConflictResolver

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_resolver():
    """Return a ConflictResolver with a minimal stub SquadConfig."""

    class FakeSquad:
        def model(self, role: str) -> str:
            return "test-model"

    return ConflictResolver(squad_cfg=FakeSquad(), messages=[])


# ---------------------------------------------------------------------------
# resolve_merge_conflict
# ---------------------------------------------------------------------------


class TestResolveMergeConflict:
    def test_success(self, tmp_path, monkeypatch):
        """Coder resolves conflict, merge completes, no exception raised."""
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), work_dev_branch="dev"))
        resolver = _make_resolver()

        with (
            patch.object(_ctx, "build_agent_context", return_value=("model", "ctx")),
            patch.object(_ctx, "invoke_agent", return_value=0),
            patch.object(_git, "_merge_in_progress", return_value=False),
        ):
            # Should NOT raise
            resolver.resolve_merge_conflict("feat/task", tmp_path, "M src/foo.py")

    def test_coder_nonzero_raises_exit(self, tmp_path, monkeypatch):
        """Coder exits non-zero → ConflictResolutionFailed raised."""
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), work_dev_branch="dev"))
        resolver = _make_resolver()

        with (
            patch.object(_ctx, "build_agent_context", return_value=("model", "ctx")),
            patch.object(_ctx, "invoke_agent", return_value=1),
        ):
            with pytest.raises(ConflictResolutionFailed):
                resolver.resolve_merge_conflict("feat/task", tmp_path, "M src/foo.py")

    def test_merge_still_in_progress_raises_exit(self, tmp_path, monkeypatch):
        """Coder succeeds but merge still in progress → ConflictResolutionFailed raised."""
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), work_dev_branch="dev"))
        resolver = _make_resolver()

        with (
            patch.object(_ctx, "build_agent_context", return_value=("model", "ctx")),
            patch.object(_ctx, "invoke_agent", return_value=0),
            patch.object(_git, "_merge_in_progress", return_value=True),
        ):
            with pytest.raises(ConflictResolutionFailed):
                resolver.resolve_merge_conflict("feat/task", tmp_path, "M src/foo.py")


# ---------------------------------------------------------------------------
# resolve_rebase_conflict
# ---------------------------------------------------------------------------


class TestResolveRebaseConflict:
    def test_success(self, tmp_path, monkeypatch):
        """Coder resolves rebase conflict, no exception raised."""
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), work_dev_branch="dev"))
        resolver = _make_resolver()

        with (
            patch.object(_ctx, "build_agent_context", return_value=("model", "ctx")),
            patch.object(_ctx, "invoke_agent", return_value=0),
            patch.object(_git, "_rebase_in_progress", return_value=False),
        ):
            resolver.resolve_rebase_conflict(tmp_path, "M src/foo.py")

    def test_coder_nonzero_raises_exit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), work_dev_branch="dev"))
        resolver = _make_resolver()

        with (
            patch.object(_ctx, "build_agent_context", return_value=("model", "ctx")),
            patch.object(_ctx, "invoke_agent", return_value=2),
        ):
            with pytest.raises(ConflictResolutionFailed):
                resolver.resolve_rebase_conflict(tmp_path, "UU src/bar.py")

    def test_rebase_still_in_progress_raises_exit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), work_dev_branch="dev"))
        resolver = _make_resolver()

        with (
            patch.object(_ctx, "build_agent_context", return_value=("model", "ctx")),
            patch.object(_ctx, "invoke_agent", return_value=0),
            patch.object(_git, "_rebase_in_progress", return_value=True),
        ):
            with pytest.raises(ConflictResolutionFailed):
                resolver.resolve_rebase_conflict(tmp_path, "UU src/bar.py")
