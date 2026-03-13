"""Tests for orc/workflow.py."""

from unittest.mock import patch

from conftest import make_msg

import orc.engine.context as _ctx
import orc.git.core as _git
import orc.messaging.telegram as tg
from orc.engine.workflow import (
    _ORC_RESOLVED_RE,
    _has_unresolved_block,
    _post_resolved,
)

# ---------------------------------------------------------------------------
# _ORC_RESOLVED_RE pattern
# ---------------------------------------------------------------------------


class TestOrcResolvedPattern:
    def test_matches_orc_resolved(self):
        assert _ORC_RESOLVED_RE.match(
            "[orc](resolved) 2026-03-09T10:00:00Z: coder(soft-blocked) addressed by planner."
        )

    def test_does_not_match_agent_resolved(self):
        assert not _ORC_RESOLVED_RE.match("[planner-1](resolved) 2026-03-09T10:00:00Z: Done.")

    def test_does_not_match_plain_text(self):
        assert not _ORC_RESOLVED_RE.match("resolved something")


# ---------------------------------------------------------------------------
# _has_unresolved_block
# ---------------------------------------------------------------------------


class TestHasUnresolvedBlock:
    def test_empty_messages_returns_none(self):
        assert _has_unresolved_block([]) == (None, None)

    def test_hard_blocked_message_detected(self):
        msgs = [make_msg("[coder-1](blocked) 2026-03-09T11:00:00Z: Stuck.")]
        assert _has_unresolved_block(msgs) == ("coder-1", "blocked")

    def test_soft_blocked_message_detected(self):
        msgs = [make_msg("[coder-1](soft-blocked) 2026-03-09T11:00:00Z: Spec unclear.")]
        assert _has_unresolved_block(msgs) == ("coder-1", "soft-blocked")

    def test_planner_blocked_detected(self):
        msgs = [make_msg("[planner-1](blocked) 2026-03-09T10:00:00Z: Need vision.")]
        assert _has_unresolved_block(msgs) == ("planner-1", "blocked")

    def test_qa_blocked_detected(self):
        msgs = [make_msg("[qa-1](blocked) 2026-03-09T12:00:00Z: Cannot review.")]
        assert _has_unresolved_block(msgs) == ("qa-1", "blocked")

    def test_orc_resolved_closes_prior_block(self):
        msgs = [
            make_msg("[coder-1](soft-blocked) 2026-03-09T11:00:00Z: Spec unclear.", ts=1000),
            make_msg(
                "[orc](resolved) 2026-03-09T11:30:00Z: coder(soft-blocked) addressed.", ts=2000
            ),
        ]
        assert _has_unresolved_block(msgs) == (None, None)

    def test_later_terminal_state_closes_prior_block(self):
        """A non-boot terminal state from any agent after a block closes the block."""
        msgs = [
            make_msg("[coder-1](soft-blocked) 2026-03-09T11:00:00Z: Spec unclear.", ts=1000),
            make_msg("[planner-1](ready) 2026-03-09T11:30:00Z: Clarified.", ts=2000),
        ]
        assert _has_unresolved_block(msgs) == (None, None)

    def test_boot_message_does_not_close_block(self):
        """A boot message between a block and now should not close the block."""
        msgs = [
            make_msg("[coder-1](soft-blocked) 2026-03-09T11:00:00Z: Spec unclear.", ts=1000),
            make_msg("[planner-1](boot) 2026-03-09T11:15:00Z: starting.", ts=2000),
        ]
        assert _has_unresolved_block(msgs) == ("coder-1", "soft-blocked")

    def test_non_blocked_state_returns_none(self):
        msgs = [make_msg("[coder-1](done) 2026-03-09T11:00:00Z: All done.")]
        assert _has_unresolved_block(msgs) == (None, None)

    def test_human_messages_ignored(self):
        msgs = [make_msg("[pietro] 2026-03-09T10:00:00Z: Go for it!")]
        assert _has_unresolved_block(msgs) == (None, None)

    def test_unknown_agent_ignored(self):
        msgs = [make_msg("[reviewer](blocked) 2026-03-09T10:00:00Z: Stuck.")]
        assert _has_unresolved_block(msgs) == (None, None)

    def test_scans_newest_to_oldest(self):
        """If the most recent message is non-blocked, no block is returned."""
        msgs = [
            make_msg("[coder-1](soft-blocked) 2026-03-09T11:00:00Z: Stuck.", ts=1000),
            make_msg("[planner-1](ready) 2026-03-09T12:00:00Z: Clarified.", ts=2000),
        ]
        assert _has_unresolved_block(msgs) == (None, None)

    def test_orc_resolved_must_be_newer_than_block(self):
        """If [orc](resolved) appears *before* the block, the block is still active."""
        msgs = [
            make_msg("[orc](resolved) 2026-03-09T10:00:00Z: earlier resolved.", ts=500),
            make_msg("[coder-1](soft-blocked) 2026-03-09T11:00:00Z: New block.", ts=2000),
        ]
        assert _has_unresolved_block(msgs) == ("coder-1", "soft-blocked")


# ---------------------------------------------------------------------------
# _post_resolved
# ---------------------------------------------------------------------------


class TestPostResolved:
    def test_sends_orc_resolved_message(self):
        sent: list[str] = []
        with patch.object(tg, "send_message", side_effect=lambda t: sent.append(t)):
            _post_resolved("coder", "soft-blocked", "planner")
        assert len(sent) == 1
        assert "[orc](resolved)" in sent[0]
        assert "coder(soft-blocked)" in sent[0]
        assert "planner" in sent[0]

    def test_resolved_message_recognized_by_pattern(self):
        sent: list[str] = []
        with patch.object(tg, "send_message", side_effect=lambda t: sent.append(t)):
            _post_resolved("coder", "soft-blocked", "planner")
        assert _ORC_RESOLVED_RE.match(sent[0])


# ---------------------------------------------------------------------------
# workflow.py coverage gap tests
# ---------------------------------------------------------------------------


class TestWorkflowCoverage:
    def test_do_close_board_crash_recovery(self, tmp_path, monkeypatch):
        """_do_close_board moves task from open to done in the board (cache write, no git)."""
        import yaml

        import orc.engine.workflow as _wf

        board_file = tmp_path / ".orc" / "work" / "board.yaml"
        board_file.write_text("counter: 1\nopen:\n  - name: 0001-foo.md\ndone: []\n")

        _wf._do_close_board("0001-foo.md")

        board = yaml.safe_load(board_file.read_text())
        open_names = [(t["name"] if isinstance(t, dict) else str(t)) for t in board.get("open", [])]
        done_names = [(t["name"] if isinstance(t, dict) else str(t)) for t in board.get("done", [])]
        assert "0001-foo.md" not in open_names
        assert "0001-foo.md" in done_names

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
