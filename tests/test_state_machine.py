"""Tests for the git-derived state machine.

Covers:
  - _derive_state_from_git     (main.py)
  - _has_unresolved_block      (main.py)
  - _post_resolved             (main.py)
  - determine_next_agent       (main.py)
  - Local chat.log read/write and get_messages merging
"""

import json
from unittest.mock import MagicMock, patch

from conftest import make_msg

import orc.config as _cfg
from orc import telegram as tg
from orc.main import (
    _ORC_RESOLVED_RE,
    _derive_state_from_git,
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
# _derive_state_from_git
# ---------------------------------------------------------------------------


class TestDeriveStateFromGit:
    def _patch(
        self,
        monkeypatch,
        *,
        active_task,
        branch_exists,
        has_commits,
        is_merged=False,
        last_commit_msg=None,
    ):
        monkeypatch.setattr("orc.board._active_task_name", lambda: active_task)
        monkeypatch.setattr("orc.git._feature_branch_exists", lambda b: branch_exists)
        monkeypatch.setattr("orc.git._feature_has_commits_ahead_of_main", lambda b: has_commits)
        monkeypatch.setattr("orc.git._feature_merged_into_dev", lambda b: is_merged)
        monkeypatch.setattr("orc.git._last_feature_commit_message", lambda b: last_commit_msg)

    def test_no_open_tasks_returns_planner(self, monkeypatch):
        self._patch(monkeypatch, active_task=None, branch_exists=False, has_commits=False)
        agent, reason = _derive_state_from_git()
        assert agent == "planner"
        assert "no open tasks" in reason

    def test_no_feature_branch_returns_coder(self, monkeypatch):
        self._patch(
            monkeypatch,
            active_task="0003-foo.md",
            branch_exists=False,
            has_commits=False,
        )
        agent, reason = _derive_state_from_git()
        assert agent == "coder"
        assert "does not exist" in reason

    def test_no_branch_but_merged_returns_close_board(self, monkeypatch):
        """Crash recovery: branch merged but board not yet updated."""
        self._patch(
            monkeypatch,
            active_task="0003-foo.md",
            branch_exists=False,
            has_commits=False,
            is_merged=True,
        )
        agent, reason = _derive_state_from_git()
        from orc.main import _CLOSE_BOARD

        assert agent == _CLOSE_BOARD
        assert "merged" in reason

    def test_feature_branch_exists_no_commits_returns_coder(self, monkeypatch):
        self._patch(
            monkeypatch,
            active_task="0003-foo.md",
            branch_exists=True,
            has_commits=False,
        )
        agent, reason = _derive_state_from_git()
        assert agent == "coder"
        assert "no commits" in reason

    def test_coder_commits_returns_qa(self, monkeypatch):
        """Coder-authored commit → route to QA."""
        self._patch(
            monkeypatch,
            active_task="0003-foo.md",
            branch_exists=True,
            has_commits=True,
            last_commit_msg="feat: implement ResourceType enum",
        )
        agent, reason = _derive_state_from_git()
        assert agent == "qa"
        assert "awaiting review" in reason

    def test_qa_passed_commit_returns_qa_passed_sentinel(self, monkeypatch):
        """qa(passed): commit → _QA_PASSED sentinel."""
        self._patch(
            monkeypatch,
            active_task="0003-foo.md",
            branch_exists=True,
            has_commits=True,
            last_commit_msg="qa(passed): no issues found",
        )
        from orc.main import _QA_PASSED

        agent, reason = _derive_state_from_git()
        assert agent == _QA_PASSED
        assert "ready to merge" in reason

    def test_qa_failed_commit_returns_coder(self, monkeypatch):
        """qa(failed): commit → route back to coder."""
        self._patch(
            monkeypatch,
            active_task="0003-foo.md",
            branch_exists=True,
            has_commits=True,
            last_commit_msg="qa(failed): missing endpoint",
        )
        agent, reason = _derive_state_from_git()
        assert agent == "coder"
        assert "issues" in reason

    def test_qa_blocked_commit_returns_coder(self, monkeypatch):
        """Any qa( prefix other than passed → route to coder."""
        self._patch(
            monkeypatch,
            active_task="0003-foo.md",
            branch_exists=True,
            has_commits=True,
            last_commit_msg="qa(blocked): cannot review without spec",
        )
        agent, reason = _derive_state_from_git()
        assert agent == "coder"

    def test_no_last_commit_message_returns_qa(self, monkeypatch):
        """No commit message (e.g. git error) → treat as coder commits, route to QA."""
        self._patch(
            monkeypatch,
            active_task="0003-foo.md",
            branch_exists=True,
            has_commits=True,
            last_commit_msg=None,
        )
        agent, _ = _derive_state_from_git()
        assert agent == "qa"

    def test_reason_includes_branch_name(self, monkeypatch):
        self._patch(
            monkeypatch,
            active_task="0003-resource-type-enum.md",
            branch_exists=True,
            has_commits=True,
            last_commit_msg="feat: add enum",
        )
        _, reason = _derive_state_from_git()
        assert "feat/0003-resource-type-enum" in reason


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
        agent, reason = determine_next_agent([])
        assert agent == "planner"
        assert "merged" in reason

    def test_block_overrides_git_state(self, monkeypatch):
        """Even if git says QA, a hard block should return None."""
        self._git_patch(monkeypatch, "qa")
        msgs = [make_msg("[qa-1](blocked) 2026-03-09T12:00:00Z: Cannot review.")]
        agent, reason = determine_next_agent(msgs)
        assert agent is None


# ---------------------------------------------------------------------------
# Local chat.log – read/write and get_messages merging (unchanged)
# ---------------------------------------------------------------------------


class TestLocalChatLog:
    """The local log is the fix for Telegram's getUpdates blindspot."""

    def test_append_and_read_log(self, tmp_path):
        log_file = tmp_path / "chat.log"
        with patch.object(tg, "_LOG_FILE", log_file):
            tg._append_to_log("hello world")
            entries = tg._read_log()

        assert len(entries) == 1
        assert entries[0]["text"] == "hello world"
        assert "date" in entries[0]
        assert entries[0]["from"]["username"] == "bot"

    def test_read_log_returns_empty_when_missing(self, tmp_path):
        log_file = tmp_path / "nonexistent.log"
        with patch.object(tg, "_LOG_FILE", log_file):
            assert tg._read_log() == []

    def test_read_log_skips_corrupt_lines(self, tmp_path):
        log_file = tmp_path / "chat.log"
        log_file.write_text('{"text": "ok"}\nnot-json\n{"text": "also ok"}\n')
        with patch.object(tg, "_LOG_FILE", log_file):
            entries = tg._read_log()
        assert len(entries) == 2
        assert entries[0]["text"] == "ok"
        assert entries[1]["text"] == "also ok"

    def test_send_message_writes_to_log(self, tmp_path):
        log_file = tmp_path / "chat.log"
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}

        with (
            patch.object(tg, "_LOG_FILE", log_file),
            patch.object(tg, "_require_config"),
            patch("httpx.Client") as mock_client_cls,
        ):
            mock_client = MagicMock()
            mock_client.__enter__ = lambda s: mock_client
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            tg.send_message("[planner-1](ready) 2026-03-09T10:00:00Z: Plan created.")

        lines = [json.loads(ln) for ln in log_file.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        assert lines[0]["text"] == "[planner-1](ready) 2026-03-09T10:00:00Z: Plan created."

    def test_get_messages_merges_log_and_telegram(self, tmp_path):
        log_file = tmp_path / "chat.log"
        bot_entry = {
            "text": "[planner-1](ready) 2026-03-09T10:00:00Z: Plan created.",
            "date": 2000,
            "from": {"username": "bot", "first_name": "bot"},
        }
        log_file.write_text(json.dumps(bot_entry) + "\n")

        human_msg = make_msg("[pietro] 2026-03-09T09:00:00Z: Start!", ts=1000)

        with (
            patch.object(tg, "_LOG_FILE", log_file),
            patch.object(tg, "_get_telegram_updates", return_value=[human_msg]),
        ):
            msgs = tg.get_messages()

        assert len(msgs) == 2
        assert msgs[0]["date"] == 1000
        assert msgs[1]["date"] == 2000

    def test_get_messages_deduplicates_by_text(self, tmp_path):
        log_file = tmp_path / "chat.log"
        msg_text = "[planner-1](ready) 2026-03-09T10:00:00Z: Plan created."
        entry = {"text": msg_text, "date": 2000, "from": {"username": "bot", "first_name": "bot"}}
        log_file.write_text(json.dumps(entry) + "\n")

        duplicate = make_msg(msg_text, ts=2000)

        with (
            patch.object(tg, "_LOG_FILE", log_file),
            patch.object(tg, "_get_telegram_updates", return_value=[duplicate]),
        ):
            msgs = tg.get_messages()

        assert len(msgs) == 1

    def test_get_messages_falls_back_to_log_when_telegram_unavailable(self, tmp_path):
        log_file = tmp_path / "chat.log"
        entry = {
            "text": "[coder-1](done) 2026-03-09T11:00:00Z: Done.",
            "date": 5000,
            "from": {"username": "bot", "first_name": "bot"},
        }
        log_file.write_text(json.dumps(entry) + "\n")

        with (
            patch.object(tg, "_LOG_FILE", log_file),
            patch.object(tg, "_get_telegram_updates", side_effect=Exception("no network")),
        ):
            msgs = tg.get_messages()

        assert len(msgs) == 1
        assert msgs[0]["text"] == "[coder-1](done) 2026-03-09T11:00:00Z: Done."


# ---------------------------------------------------------------------------
# Board reconciliation on merge
# ---------------------------------------------------------------------------


class TestBoardReconciliation:
    def test_close_task_moves_to_done(self, tmp_path, monkeypatch):
        from orc.main import _close_task_on_board

        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path / ".orc")
        monkeypatch.setattr(_cfg, "REPO_ROOT", tmp_path)

        board_path = tmp_path / ".orc" / "work" / "board.yaml"
        board_path.parent.mkdir(parents=True)
        existing_done = (
            "done:\n  - name: 0002-bar.md\n    commit-tag: abc\n"
            "    timestamp: 2026-01-01T00:00:00Z\n"
        )
        board_path.write_text("counter: 2\nopen:\n  - name: 0003-foo.md\n" + existing_done)

        _close_task_on_board("0003-foo.md", tmp_path, commit_tag="deadbeef")

        import yaml

        board = yaml.safe_load(board_path.read_text())
        names_open = [t["name"] if isinstance(t, dict) else str(t) for t in board["open"]]
        names_done = [t["name"] if isinstance(t, dict) else str(t) for t in board["done"]]
        assert "0003-foo.md" not in names_open
        assert "0003-foo.md" in names_done
        # Verify commit-tag was recorded
        done_entry = next(t for t in board["done"] if t.get("name") == "0003-foo.md")
        assert done_entry["commit-tag"] == "deadbeef"

    def test_close_task_deletes_md_file(self, tmp_path, monkeypatch):
        from orc.main import _close_task_on_board

        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path / ".orc")
        monkeypatch.setattr(_cfg, "REPO_ROOT", tmp_path)

        board_path = tmp_path / ".orc" / "work" / "board.yaml"
        board_path.parent.mkdir(parents=True)
        board_path.write_text("counter: 1\nopen:\n  - name: 0003-foo.md\ndone: []\n")
        task_md = tmp_path / ".orc" / "work" / "0003-foo.md"
        task_md.write_text("# Task\n")

        _close_task_on_board("0003-foo.md", tmp_path, commit_tag="abc123")

        assert not task_md.exists()

    def test_close_task_missing_board_does_not_raise(self, tmp_path, monkeypatch):
        from orc.main import _close_task_on_board

        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path / ".orc")
        monkeypatch.setattr(_cfg, "REPO_ROOT", tmp_path)

        # Should not raise even when board.yaml doesn't exist
        _close_task_on_board("0003-foo.md", tmp_path, commit_tag="abc")

    def test_close_task_other_tasks_preserved(self, tmp_path, monkeypatch):
        from orc.main import _close_task_on_board

        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path / ".orc")
        monkeypatch.setattr(_cfg, "REPO_ROOT", tmp_path)

        board_path = tmp_path / ".orc" / "work" / "board.yaml"
        board_path.parent.mkdir(parents=True)
        board_path.write_text(
            "counter: 3\nopen:\n  - name: 0003-foo.md\n  - name: 0004-bar.md\ndone: []\n"
        )

        _close_task_on_board("0003-foo.md", tmp_path, commit_tag="abc")

        import yaml

        board = yaml.safe_load(board_path.read_text())
        names_open = [t["name"] if isinstance(t, dict) else str(t) for t in board["open"]]
        assert "0004-bar.md" in names_open
        assert "0003-foo.md" not in names_open
