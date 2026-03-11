"""Tests for orc/workflow.py."""

from unittest.mock import patch

from conftest import make_msg

import orc.config as _cfg
import orc.context as _ctx
import orc.git as _git
import orc.telegram as tg
from orc.workflow import (
    _ORC_RESOLVED_RE,
    _has_unresolved_block,
    _post_resolved,
    determine_next_agent,
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
# determine_next_agent
# ---------------------------------------------------------------------------


class TestDetermineNextAgent:
    def _git_patch(self, monkeypatch, agent: str, reason: str = "test"):
        monkeypatch.setattr("orc.git._derive_state_from_git", lambda: (agent, reason))

    def test_no_messages_falls_through_to_git(self, monkeypatch):
        self._git_patch(monkeypatch, "coder")
        agent, _ = determine_next_agent([])
        assert agent == "coder"

    def test_unresolved_hard_block_returns_none(self, monkeypatch):
        self._git_patch(monkeypatch, "qa")
        msgs = [make_msg("[planner-1](blocked) 2026-03-09T10:00:00Z: Need vision.")]
        agent, reason = determine_next_agent(msgs)
        assert agent is None
        assert "human intervention" in reason

    def test_unresolved_soft_block_returns_planner(self, monkeypatch):
        self._git_patch(monkeypatch, "qa")
        msgs = [make_msg("[coder-1](soft-blocked) 2026-03-09T11:00:00Z: Unclear spec.")]
        agent, reason = determine_next_agent(msgs)
        assert agent == "planner"
        assert "soft-blocked" in reason

    def test_resolved_block_falls_through_to_git(self, monkeypatch):
        self._git_patch(monkeypatch, "qa", "feature branch has unreviewed commits")
        msgs = [
            make_msg("[coder-1](soft-blocked) 2026-03-09T11:00:00Z: Stuck.", ts=1000),
            make_msg("[orc](resolved) 2026-03-09T12:00:00Z: resolved.", ts=2000),
        ]
        agent, reason = determine_next_agent(msgs)
        assert agent == "qa"

    def test_git_routing_no_tasks(self, monkeypatch):
        self._git_patch(monkeypatch, "planner", "no open tasks on board")
        monkeypatch.setattr(_ctx, "_has_planner_work", lambda: True)
        agent, reason = determine_next_agent([])
        assert agent == "planner"
        assert "no open tasks" in reason

    def test_git_routing_no_branch(self, monkeypatch):
        self._git_patch(monkeypatch, "coder", "feature branch does not exist yet")
        agent, reason = determine_next_agent([])
        assert agent == "coder"

    def test_git_routing_with_commits(self, monkeypatch):
        self._git_patch(monkeypatch, "qa", "feature branch has unreviewed commits")
        agent, reason = determine_next_agent([])
        assert agent == "qa"

    def test_git_routing_merged(self, monkeypatch):
        self._git_patch(monkeypatch, "planner", "feature branch already merged into dev")
        monkeypatch.setattr(_ctx, "_has_planner_work", lambda: True)
        agent, reason = determine_next_agent([])
        assert agent == "planner"
        assert "merged" in reason

    def test_planner_skipped_when_no_work(self, monkeypatch):
        """Planner is skipped when there are no vision docs or TODOs/FIXMEs."""
        self._git_patch(monkeypatch, "planner", "no open tasks on board")
        monkeypatch.setattr(_ctx, "_has_planner_work", lambda: False)
        agent, reason = determine_next_agent([])
        assert agent is None
        assert "nothing to plan" in reason

    def test_block_overrides_git_state(self, monkeypatch):
        """Even if git says QA, a hard block should return None."""
        self._git_patch(monkeypatch, "qa")
        msgs = [make_msg("[qa-1](blocked) 2026-03-09T12:00:00Z: Cannot review.")]
        agent, reason = determine_next_agent(msgs)
        assert agent is None


# ---------------------------------------------------------------------------
# workflow.py coverage gap tests
# ---------------------------------------------------------------------------


class TestWorkflowCoverage:
    def test_do_close_board_crash_recovery(self, tmp_path, monkeypatch):
        """Lines 74-81: crash-recovery closes board and commits."""
        from unittest.mock import MagicMock
        from unittest.mock import patch as _patch

        import orc.workflow as _wf

        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path / "orc")
        monkeypatch.setattr(_cfg, "REPO_ROOT", tmp_path)

        dev_wt = tmp_path / "dev"
        orc_work = dev_wt / "orc" / "work"
        orc_work.mkdir(parents=True)
        (orc_work / "board.yaml").write_text("counter: 1\nopen:\n  - name: 0001-foo.md\ndone: []\n")

        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: dev_wt)
        runs: list[list] = []

        def fake_run(cmd, cwd=None, check=False, **kw):
            runs.append(cmd)
            r = MagicMock()
            r.returncode = 0
            return r

        with _patch("orc.workflow.subprocess.run", fake_run):
            _wf._do_close_board("0001-foo.md")

        cmds = [" ".join(c) for c in runs]
        assert any("add" in c for c in cmds)
        assert any("commit" in c for c in cmds)


# ---------------------------------------------------------------------------
# _make_merge_feature_fn
# ---------------------------------------------------------------------------


class TestMakeMergeFeatureFn:
    def _make_squad(self, monkeypatch):
        from unittest.mock import MagicMock

        squad = MagicMock()
        squad.model.return_value = "claude-sonnet-4.6"
        return squad

    def test_delegates_to_merge_feature_into_dev(self, monkeypatch, tmp_path):
        """Happy path: no conflict, calls _git._merge_feature_into_dev."""
        import orc.workflow as _wf

        calls = []
        monkeypatch.setattr(_git, "_merge_feature_into_dev", lambda t: calls.append(t))
        squad = self._make_squad(monkeypatch)

        fn = _wf._make_merge_feature_fn(squad)
        fn("0001-foo.md")

        assert calls == ["0001-foo.md"]

    def test_spawns_coder_on_merge_conflict(self, monkeypatch, tmp_path):
        """On MergeConflictError, a coder agent is invoked."""
        import orc.context as _ctx
        import orc.workflow as _wf

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

        import orc.context as _ctx
        import orc.workflow as _wf

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

        import orc.context as _ctx
        import orc.workflow as _wf

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
