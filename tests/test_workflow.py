"""Tests for orc/workflow.py."""

import orc.engine.context as _ctx
import orc.git.core as _git
import orc.messaging.telegram as tg

# ---------------------------------------------------------------------------
# workflow.py coverage gap tests
# ---------------------------------------------------------------------------


class TestWorkflowCoverage:
    def test_do_close_board_crash_recovery(self, tmp_path, monkeypatch):
        """_do_close_board removes task from the board (cache write, no git)."""
        import yaml

        import orc.engine.workflow as _wf

        board_file = tmp_path / ".orc" / "work" / "board.yaml"
        board_file.write_text("counter: 1\ntasks:\n  - name: 0001-foo.md\n")

        _wf._do_close_board("0001-foo.md")

        board = yaml.safe_load(board_file.read_text())
        task_names = [
            (t["name"] if isinstance(t, dict) else str(t)) for t in board.get("tasks", [])
        ]
        assert "0001-foo.md" not in task_names

    def test_do_close_board_task_not_on_board_does_not_raise(self, tmp_path):
        """_do_close_board is a no-op (no crash) when task isn't on the open list."""
        import orc.engine.workflow as _wf

        # board.yaml not created → empty board → no crash
        _wf._do_close_board("0001-missing.md")


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
