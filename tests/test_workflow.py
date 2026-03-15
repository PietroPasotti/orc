"""Tests for orc/workflow.py."""

import orc.engine.context as _ctx
import orc.git.core as _git
import orc.messaging.telegram as tg


class TestMakeMergeFeatureFn:
    def _make_squad(self, monkeypatch):
        from unittest.mock import MagicMock

        squad = MagicMock()
        squad.model.return_value = "claude-sonnet-4.6"
        return squad

    def test_delegates_to_merge_feature_into_dev(self, monkeypatch, tmp_path):
        """Happy path: no conflict, calls _git._merge_feature_into_dev."""
        import orc.engine.workflow as _wf

        calls = []
        monkeypatch.setattr(_git, "_merge_feature_into_dev", lambda t: calls.append(t))
        squad = self._make_squad(monkeypatch)

        fn = _wf._make_merge_feature_fn(squad)
        fn("0001-foo.md")

        assert calls == ["0001-foo.md"]

    def test_spawns_coder_on_merge_conflict(self, monkeypatch, tmp_path):
        """On MergeConflictError, a coder agent is invoked."""
        import orc.engine.workflow as _wf

        exc = _git.MergeConflictError("feat/0001-foo", tmp_path, "UU src/foo.py")
        monkeypatch.setattr(_git, "_merge_feature_into_dev", lambda t: (_ for _ in ()).throw(exc))
        monkeypatch.setattr(_git, "_merge_in_progress", lambda p: False)
        monkeypatch.setattr(tg, "get_messages", lambda: [])
        monkeypatch.setattr(_ctx, "build_agent_context", lambda *a, **kw: ("model", "ctx"))
        monkeypatch.setattr(_ctx, "invoke_agent", lambda *a, **kw: 0)
        squad = self._make_squad(monkeypatch)

        fn = _wf._make_merge_feature_fn(squad)
        fn("0001-foo.md")  # should not raise

    def test_raises_exit_when_coder_fails(self, monkeypatch, tmp_path):
        """If coder exits non-zero, typer.Exit is raised."""
        import pytest
        import typer

        import orc.engine.workflow as _wf

        exc = _git.MergeConflictError("feat/0001-foo", tmp_path, "UU src/foo.py")
        monkeypatch.setattr(_git, "_merge_feature_into_dev", lambda t: (_ for _ in ()).throw(exc))
        monkeypatch.setattr(tg, "get_messages", lambda: [])
        monkeypatch.setattr(_ctx, "build_agent_context", lambda *a, **kw: ("model", "ctx"))
        monkeypatch.setattr(_ctx, "invoke_agent", lambda *a, **kw: 1)
        squad = self._make_squad(monkeypatch)

        fn = _wf._make_merge_feature_fn(squad)
        with pytest.raises(typer.Exit):
            fn("0001-foo.md")

    def test_raises_exit_when_merge_still_in_progress_after_coder(self, monkeypatch, tmp_path):
        """If merge is still in progress after coder exits, typer.Exit is raised."""
        import pytest
        import typer

        import orc.engine.workflow as _wf

        exc = _git.MergeConflictError("feat/0001-foo", tmp_path, "UU src/foo.py")
        monkeypatch.setattr(_git, "_merge_feature_into_dev", lambda t: (_ for _ in ()).throw(exc))
        monkeypatch.setattr(_git, "_merge_in_progress", lambda p: True)
        monkeypatch.setattr(tg, "get_messages", lambda: [])
        monkeypatch.setattr(_ctx, "build_agent_context", lambda *a, **kw: ("model", "ctx"))
        monkeypatch.setattr(_ctx, "invoke_agent", lambda *a, **kw: 0)
        squad = self._make_squad(monkeypatch)

        fn = _wf._make_merge_feature_fn(squad)
        with pytest.raises(typer.Exit):
            fn("0001-foo.md")
