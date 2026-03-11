"""Tests for orc/context.py."""

import time

from conftest import make_msg

import orc.config as _cfg
import orc.context as _ctx
import orc.git as _git
import orc.telegram as tg

# ---------------------------------------------------------------------------
# _boot_message_body
# ---------------------------------------------------------------------------


class TestBootMessageBody:
    def test_single_open_task(self, tmp_path, monkeypatch):
        board = tmp_path / "board.yaml"
        board.write_text("counter: 2\nopen:\n  - name: 0002-foo.md\n")
        monkeypatch.setattr(_cfg, "BOARD_FILE", board)
        assert _ctx._boot_message_body() == "picking up work/0002-foo.md."

    def test_multiple_open_tasks(self, tmp_path, monkeypatch):
        board = tmp_path / "board.yaml"
        board.write_text("counter: 3\nopen:\n  - name: 0002-foo.md\n  - name: 0003-bar.md\n")
        monkeypatch.setattr(_cfg, "BOARD_FILE", board)
        assert _ctx._boot_message_body() == "picking up work/0002-foo.md, work/0003-bar.md."

    def test_no_open_tasks(self, tmp_path, monkeypatch):
        board = tmp_path / "board.yaml"
        board.write_text("counter: 2\nopen: []\n")
        monkeypatch.setattr(_cfg, "BOARD_FILE", board)
        assert _ctx._boot_message_body() == "no open tasks on board."

    def test_missing_board(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_cfg, "BOARD_FILE", tmp_path / "nonexistent.yaml")
        assert _ctx._boot_message_body() == "no open tasks on board."


# ---------------------------------------------------------------------------
# wait_for_human_reply
# ---------------------------------------------------------------------------


class TestWaitForHumanReply:
    def _human(self, text: str, ts: int) -> dict:
        return {"text": text, "date": ts, "from": {"username": "pietro", "first_name": "Pietro"}}

    def test_returns_first_new_human_message(self, monkeypatch):
        snapshot = [make_msg("[coder-1](blocked) 2026-03-09T11:00:00Z: Need help.", ts=1000)]
        human = self._human("Here is the clarification.", ts=2000)
        monkeypatch.setattr(tg, "get_messages", lambda: snapshot + [human])
        times = iter([0.0, 1.0])
        monkeypatch.setattr(time, "monotonic", lambda: next(times))
        monkeypatch.setattr(time, "sleep", lambda s: None)

        result = _ctx.wait_for_human_reply(snapshot, timeout=3600.0)
        assert result == "Here is the clarification."

    def test_skips_snapshot_messages(self, monkeypatch):
        old_human = self._human("old message", ts=500)
        snapshot = [old_human]
        new_human = self._human("new message", ts=600)
        monkeypatch.setattr(tg, "get_messages", lambda: snapshot + [new_human])
        times = iter([0.0, 1.0])
        monkeypatch.setattr(time, "monotonic", lambda: next(times))
        monkeypatch.setattr(time, "sleep", lambda s: None)

        result = _ctx.wait_for_human_reply(snapshot, timeout=3600.0)
        assert result == "new message"

    def test_skips_agent_messages(self, monkeypatch):
        snapshot = [make_msg("[coder-1](blocked) 2026-03-09T11:00:00Z: Blocked.", ts=1000)]
        agent_msg = make_msg("[planner-1](ready) 2026-03-09T11:30:00Z: ADR updated.", ts=2000)
        human_msg = self._human("Please continue.", ts=3000)
        call_count = 0

        def get_messages():
            nonlocal call_count
            call_count += 1
            return snapshot + [agent_msg] if call_count == 1 else snapshot + [agent_msg, human_msg]

        monkeypatch.setattr(tg, "get_messages", get_messages)
        times = iter([0.0, 1.0, 2.0])
        monkeypatch.setattr(time, "monotonic", lambda: next(times))
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        result = _ctx.wait_for_human_reply(snapshot, initial_delay=5.0, timeout=3600.0)
        assert result == "Please continue."
        assert len(sleeps) == 2

    def test_exponential_backoff(self, monkeypatch):
        snapshot: list[dict] = []
        human = self._human("Done.", ts=9000)
        call_count = 0

        def get_messages():
            nonlocal call_count
            call_count += 1
            return snapshot if call_count < 3 else [human]

        monkeypatch.setattr(tg, "get_messages", get_messages)
        times = iter([0.0, 1.0, 2.0, 3.0])
        monkeypatch.setattr(time, "monotonic", lambda: next(times))
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        _ctx.wait_for_human_reply(
            snapshot, initial_delay=5.0, backoff_factor=2.0, max_delay=300.0, timeout=3600.0
        )
        assert sleeps == [5.0, 10.0, 20.0]

    def test_backoff_capped_at_max_delay(self, monkeypatch):
        snapshot: list[dict] = []
        human = self._human("Done.", ts=9000)
        call_count = 0

        def get_messages():
            nonlocal call_count
            call_count += 1
            return snapshot if call_count < 4 else [human]

        monkeypatch.setattr(tg, "get_messages", get_messages)
        times = iter([0.0, 1.0, 2.0, 3.0, 4.0])
        monkeypatch.setattr(time, "monotonic", lambda: next(times))
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        _ctx.wait_for_human_reply(
            snapshot, initial_delay=5.0, backoff_factor=2.0, max_delay=10.0, timeout=3600.0
        )
        assert sleeps == [5.0, 10.0, 10.0, 10.0]

    def test_raises_timeout_error(self, monkeypatch):
        import pytest

        snapshot: list[dict] = []
        monkeypatch.setattr(tg, "get_messages", lambda: snapshot)
        times = iter([0.0, 3601.0])
        monkeypatch.setattr(time, "monotonic", lambda: next(times))
        monkeypatch.setattr(time, "sleep", lambda s: None)

        with pytest.raises(TimeoutError):
            _ctx.wait_for_human_reply(snapshot, timeout=3600.0)

    def test_sleep_trimmed_to_deadline(self, monkeypatch):
        """Sleep must not overshoot the deadline."""
        import pytest

        snapshot: list[dict] = []
        monkeypatch.setattr(tg, "get_messages", lambda: snapshot)
        times = iter([0.0, 9.0, 10.1])
        monkeypatch.setattr(time, "monotonic", lambda: next(times))
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        with pytest.raises(TimeoutError):
            _ctx.wait_for_human_reply(snapshot, initial_delay=300.0, timeout=10.0)

        assert sleeps == [1.0]


# ---------------------------------------------------------------------------
# Coverage tests for context.py helpers
# ---------------------------------------------------------------------------


class TestContextCoverage:
    def test_read_adrs_empty_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path / ".orc")
        result = _ctx._read_adrs()
        assert result == "_No ADRs found._"

    def test_read_adrs_with_files(self, tmp_path, monkeypatch):
        adr_dir = tmp_path / "docs" / "adr"
        adr_dir.mkdir(parents=True)
        (adr_dir / "001-decision.md").write_text("# ADR 001\n\nSome decision.")
        (adr_dir / "README.md").write_text("# ADRs index")
        monkeypatch.setattr(_cfg, "REPO_ROOT", tmp_path)
        result = _ctx._read_adrs()
        assert "001-decision.md" in result
        assert "README.md" not in result

    def test_parse_role_file_missing_returns_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_cfg, "ROLES_DIR", tmp_path / "roles")
        monkeypatch.setattr(_cfg, "_PACKAGE_ROLES_DIR", tmp_path / "pkg_roles")
        result = _ctx._parse_role_file("wizard")
        assert "wizard" in result

    def test_parse_role_file_with_frontmatter(self, tmp_path, monkeypatch):
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        (roles_dir / "coder.md").write_text("---\nsymbol: 🧑‍💻\n---\nYou are the coder agent.")
        monkeypatch.setattr(_cfg, "ROLES_DIR", roles_dir)
        result = _ctx._parse_role_file("coder")
        assert "coder agent" in result
        assert "symbol" not in result

    def test_role_symbol_returns_empty_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_cfg, "ROLES_DIR", tmp_path / "roles")
        monkeypatch.setattr(_cfg, "_PACKAGE_ROLES_DIR", tmp_path / "pkg_roles")
        assert _ctx._role_symbol("wizard") == ""

    def test_role_symbol_no_frontmatter(self, tmp_path, monkeypatch):
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        (roles_dir / "coder.md").write_text("You are the coder.")
        monkeypatch.setattr(_cfg, "ROLES_DIR", roles_dir)
        monkeypatch.setattr(_cfg, "_PACKAGE_ROLES_DIR", tmp_path / "pkg_roles")
        assert _ctx._role_symbol("coder") == ""

    def test_role_symbol_frontmatter_no_end(self, tmp_path, monkeypatch):
        """Frontmatter with no closing --- → symbol not extracted."""
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        (roles_dir / "coder.md").write_text("---\nsymbol: 🧑‍💻\nno closing marker")
        monkeypatch.setattr(_cfg, "ROLES_DIR", roles_dir)
        monkeypatch.setattr(_cfg, "_PACKAGE_ROLES_DIR", tmp_path / "pkg_roles")
        assert _ctx._role_symbol("coder") == ""

    def test_build_agent_context_planner(self, tmp_path, monkeypatch):
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        monkeypatch.setattr(_cfg, "ROLES_DIR", roles_dir)
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path / ".orc")
        monkeypatch.setattr(_cfg, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(_cfg, "WORK_DIR", tmp_path / ".orc" / "work")
        monkeypatch.setattr(_cfg, "BOARD_FILE", tmp_path / ".orc" / "work" / "board.yaml")
        (tmp_path / ".orc" / "work").mkdir(parents=True)
        (tmp_path / ".orc" / "work" / "board.yaml").write_text("open: []\ndone: []\n")
        monkeypatch.setattr(tg, "get_messages", lambda: [])
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)
        model, ctx = _ctx.build_agent_context("planner", [], worktree=tmp_path)
        assert isinstance(ctx, str)
        assert len(ctx) > 0

    def test_role_symbol_with_symbol_in_frontmatter(self, tmp_path, monkeypatch):
        """Lines 65-67: role file has valid frontmatter containing 'symbol' key."""
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        (roles_dir / "coder.md").write_text("---\nsymbol: 🧑‍💻\n---\nYou are a coder.\n")
        monkeypatch.setattr(_cfg, "ROLES_DIR", roles_dir)
        monkeypatch.setattr(_cfg, "_PACKAGE_ROLES_DIR", tmp_path / "pkg_roles")
        assert _ctx._role_symbol("coder") == "🧑‍💻"

    def test_build_context_planner_with_feature_branch(self, tmp_path, monkeypatch):
        """Line 136: else-branch with feature_branch set (agent_name not coder/qa)."""
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        work_dir = tmp_path / ".orc" / "work"
        work_dir.mkdir(parents=True)
        (work_dir / "board.yaml").write_text(
            "open:\n  - name: 0001-task.md\n    assigned_to: null\ndone: []\n"
        )
        monkeypatch.setattr(_cfg, "ROLES_DIR", roles_dir)
        monkeypatch.setattr(_cfg, "AGENTS_DIR", tmp_path / ".orc")
        monkeypatch.setattr(_cfg, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(_cfg, "WORK_DIR", work_dir)
        monkeypatch.setattr(_cfg, "BOARD_FILE", work_dir / "board.yaml")
        monkeypatch.setattr(tg, "get_messages", lambda: [])
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)
        monkeypatch.setattr(_git, "_feature_branch", lambda t: "feature/0001-task")
        monkeypatch.setattr(_git, "_feature_worktree_path", lambda t: tmp_path / "feat")
        _, ctx = _ctx.build_agent_context("planner", [], worktree=tmp_path)
        assert "feature/0001-task" in ctx
