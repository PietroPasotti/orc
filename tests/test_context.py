"""Tests for orc/context.py."""

import subprocess
import time
from dataclasses import replace as _replace

import pytest
from conftest import make_msg

import orc.config as _cfg
import orc.engine.context as _ctx
import orc.messaging.telegram as tg
from orc.engine.context import TodoItem
from orc.messaging.messages import ChatMessage

# ---------------------------------------------------------------------------
# _boot_message_body
# ---------------------------------------------------------------------------


class TestBootMessageBody:
    def _write_board(self, content: str) -> None:
        board = _cfg.get().work_dir / "board.yaml"
        board.parent.mkdir(parents=True, exist_ok=True)
        board.write_text(content)

    def _make_board(self):
        from orc.coordination.state import BoardStateManager

        return BoardStateManager(_cfg.get().orc_dir)

    @pytest.mark.parametrize(
        "agent_id,board_content,expected",
        [
            (
                "orc",
                "counter: 2\ntasks:\n  - name: 0002-foo.md\n",
                "picking up work/0002-foo.md.",
            ),
            (
                "orc",
                "counter: 3\ntasks:\n  - name: 0002-foo.md\n  - name: 0003-bar.md\n",
                "picking up work/0002-foo.md, work/0003-bar.md.",
            ),
            ("orc", "counter: 2\ntasks: []\n", "no open tasks on board."),
            ("orc", None, "no open tasks on board."),  # missing board
            (
                "planner-1",
                "counter: 2\ntasks:\n  - name: 0002-foo.md\n",
                "planning 0002-foo.md.",
            ),
            ("planner-1", "counter: 2\ntasks: []\n", "no open tasks on board."),
            (
                "coder-1",
                "counter: 2\ntasks:\n  - name: 0002-foo.md\n",
                "picking up work/0002-foo.md.",
            ),
            ("coder-1", "counter: 2\ntasks: []\n", "no open tasks on board."),
            (
                "qa-1",
                "counter: 2\ntasks:\n  - name: 0002-foo.md\n",
                "reviewing feat/0002-foo.",
            ),
            ("qa-1", "counter: 2\ntasks: []\n", "no open tasks on board."),
        ],
    )
    def test_boot_message_body(self, agent_id, board_content, expected):
        if board_content is not None:
            self._write_board(board_content)
        assert _ctx._boot_message_body(agent_id, self._make_board()) == expected

    def test_boot_message_body_planner_with_vision(self):
        """Planner with no tasks but a pending vision → 'translating vision docs.'"""
        self._write_board("counter: 2\ntasks: []\n")
        vision_ready = _cfg.get().orc_dir / "vision" / "ready"
        vision_ready.mkdir(parents=True, exist_ok=True)
        (vision_ready / "my-feature.md").write_text("# Vision\n")
        assert (
            _ctx._boot_message_body("planner-1", self._make_board()) == "translating vision docs."
        )


# ---------------------------------------------------------------------------
# wait_for_human_reply
# ---------------------------------------------------------------------------


class TestWaitForHumanReply:
    def _human(self, text: str, ts: int) -> ChatMessage:
        return ChatMessage(text=text, date=ts, sender_name="Pietro")

    def _patch_configured(self, monkeypatch) -> None:
        monkeypatch.setattr(tg, "_is_configured", lambda: True)

    def _mock_time(self, monkeypatch, values: list[float]) -> list[float]:
        """Mock time.monotonic() and time.sleep(), returning sleeps list."""
        times = iter(values)
        monkeypatch.setattr(time, "monotonic", lambda: next(times))
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
        return sleeps

    def test_returns_first_new_human_message(self, monkeypatch):
        self._patch_configured(monkeypatch)
        snapshot = [make_msg("[coder-1](blocked) 2026-03-09T11:00:00Z: Need help.", ts=1000)]
        human = self._human("Here is the clarification.", ts=2000)
        monkeypatch.setattr(tg, "_get_messages", lambda limit=100: snapshot + [human])
        self._mock_time(monkeypatch, [0.0, 1.0])

        result = _ctx.wait_for_human_reply(snapshot, timeout=3600.0)
        assert result == "Here is the clarification."

    def test_skips_snapshot_messages(self, monkeypatch):
        self._patch_configured(monkeypatch)
        old_human = self._human("old message", ts=500)
        snapshot = [old_human]
        new_human = self._human("new message", ts=600)
        monkeypatch.setattr(tg, "_get_messages", lambda limit=100: snapshot + [new_human])
        self._mock_time(monkeypatch, [0.0, 1.0])

        result = _ctx.wait_for_human_reply(snapshot, timeout=3600.0)
        assert result == "new message"

    def test_skips_agent_messages(self, monkeypatch):
        self._patch_configured(monkeypatch)
        snapshot = [make_msg("[coder-1](blocked) 2026-03-09T11:00:00Z: Blocked.", ts=1000)]
        agent_msg = make_msg("[planner-1](ready) 2026-03-09T11:30:00Z: ADR updated.", ts=2000)
        human_msg = self._human("Please continue.", ts=3000)
        call_count = 0

        def get_messages(limit=100):
            nonlocal call_count
            call_count += 1
            return snapshot + [agent_msg] if call_count == 1 else snapshot + [agent_msg, human_msg]

        monkeypatch.setattr(tg, "_get_messages", get_messages)
        sleeps = self._mock_time(monkeypatch, [0.0, 1.0, 2.0])

        result = _ctx.wait_for_human_reply(snapshot, initial_delay=5.0, timeout=3600.0)
        assert result == "Please continue."
        assert len(sleeps) == 2

    def test_exponential_backoff(self, monkeypatch):
        self._patch_configured(monkeypatch)
        snapshot: list[ChatMessage] = []
        human = self._human("Done.", ts=9000)
        call_count = 0

        def get_messages(limit=100):
            nonlocal call_count
            call_count += 1
            return snapshot if call_count < 3 else [human]

        monkeypatch.setattr(tg, "_get_messages", get_messages)
        sleeps = self._mock_time(monkeypatch, [0.0, 1.0, 2.0, 3.0])

        _ctx.wait_for_human_reply(
            snapshot, initial_delay=5.0, backoff_factor=2.0, max_delay=300.0, timeout=3600.0
        )
        assert sleeps == [5.0, 10.0, 20.0]

    def test_backoff_capped_at_max_delay(self, monkeypatch):
        self._patch_configured(monkeypatch)
        snapshot: list[ChatMessage] = []
        human = self._human("Done.", ts=9000)
        call_count = 0

        def get_messages(limit=100):
            nonlocal call_count
            call_count += 1
            return snapshot if call_count < 4 else [human]

        monkeypatch.setattr(tg, "_get_messages", get_messages)
        sleeps = self._mock_time(monkeypatch, [0.0, 1.0, 2.0, 3.0, 4.0])

        _ctx.wait_for_human_reply(
            snapshot, initial_delay=5.0, backoff_factor=2.0, max_delay=10.0, timeout=3600.0
        )
        assert sleeps == [5.0, 10.0, 10.0, 10.0]

    def test_raises_timeout_error(self, monkeypatch):
        import pytest

        self._patch_configured(monkeypatch)
        snapshot: list[ChatMessage] = []
        monkeypatch.setattr(tg, "_get_messages", lambda limit=100: snapshot)
        self._mock_time(monkeypatch, [0.0, 3601.0])

        with pytest.raises(TimeoutError):
            _ctx.wait_for_human_reply(snapshot, timeout=3600.0)

    def test_sleep_trimmed_to_deadline(self, monkeypatch):
        """Sleep must not overshoot the deadline."""
        import pytest

        self._patch_configured(monkeypatch)
        snapshot: list[ChatMessage] = []
        monkeypatch.setattr(tg, "_get_messages", lambda limit=100: snapshot)
        sleeps = self._mock_time(monkeypatch, [0.0, 9.0, 10.1])

        with pytest.raises(TimeoutError):
            _ctx.wait_for_human_reply(snapshot, initial_delay=300.0, timeout=10.0)

        assert sleeps == [1.0]

    def test_not_configured_raises_timeout_immediately(self, monkeypatch):
        """Without Telegram, wait_for_human_reply raises TimeoutError immediately."""
        import pytest

        monkeypatch.setattr(tg, "_is_configured", lambda: False)
        with pytest.raises(TimeoutError, match="not configured"):
            _ctx.wait_for_human_reply([], timeout=3600.0)

    def test_default_timeout_reads_from_config(self, monkeypatch):
        """When timeout is omitted, the value is read from orc.config."""
        self._patch_configured(monkeypatch)
        snapshot = [self._human("hi", ts=1)]
        human = self._human("reply", ts=2)
        monkeypatch.setattr(tg, "get_messages", lambda: snapshot + [human])
        self._mock_time(monkeypatch, [0.0, 1.0])
        # Call without explicit timeout — should use config default (3600.0)
        result = _ctx.wait_for_human_reply(snapshot)
        assert result == "reply"


# ---------------------------------------------------------------------------
# Coverage tests for context.py helpers
# ---------------------------------------------------------------------------


class TestContextCoverage:
    def _setup_context(
        self, monkeypatch, tmp_path, *, agents_dir=None, board_content="tasks: []\n"
    ):
        """Set up full context with config, directories, and mocks."""
        if agents_dir is None:
            agents_dir = tmp_path / "agents"
            agents_dir.mkdir(exist_ok=True)
        work_dir = tmp_path / ".orc" / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "board.yaml").write_text(board_content)
        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(
                _cfg.get(),
                agents_dir=agents_dir,
                orc_dir=tmp_path / ".orc",
                repo_root=tmp_path,
                work_dir=work_dir,
                board_file=work_dir / "board.yaml",
            ),
        )
        monkeypatch.setattr(tg, "_get_messages", lambda limit=100: [])
        monkeypatch.setattr("orc.git.Git.ensure_worktree", lambda self, worktree, branch: None)

    def _setup_agents(self, monkeypatch, tmp_path):
        """Set up minimal agents configuration."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(exist_ok=True)
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), agents_dir=agents_dir))
        monkeypatch.setattr(_cfg, "_PACKAGE_AGENTS_DIR", tmp_path / "pkg_agents")
        return agents_dir

    def test_role_symbol_directory_format(self, tmp_path, monkeypatch):
        """_role_symbol reads from _main.md when role is a directory."""
        agents_dir = self._setup_agents(monkeypatch, tmp_path)
        role_dir = agents_dir / "coder"
        role_dir.mkdir(parents=True, exist_ok=True)
        (role_dir / "_main.md").write_text("---\nsymbol: 🛠️\n---\nYou are a coder.\n")
        assert _ctx._role_symbol("coder") == "🛠️"

    def test_role_symbol_returns_empty_when_no_file(self, tmp_path, monkeypatch):
        self._setup_agents(monkeypatch, tmp_path)
        assert _ctx._role_symbol("wizard") == ""

    def test_role_symbol_no_frontmatter(self, tmp_path, monkeypatch):
        agents_dir = self._setup_agents(monkeypatch, tmp_path)
        (agents_dir / "coder.md").write_text("You are the coder.")
        assert _ctx._role_symbol("coder") == ""

    def test_role_symbol_frontmatter_no_end(self, tmp_path, monkeypatch):
        """Frontmatter with no closing --- → symbol not extracted."""
        agents_dir = self._setup_agents(monkeypatch, tmp_path)
        (agents_dir / "coder.md").write_text("---\nsymbol: 🧑‍💻\nno closing marker")
        assert _ctx._role_symbol("coder") == ""

    def test_role_symbol_with_symbol_in_frontmatter(self, tmp_path, monkeypatch):
        """Role file has valid frontmatter containing 'symbol' key."""
        agents_dir = self._setup_agents(monkeypatch, tmp_path)
        (agents_dir / "coder.md").write_text("---\nsymbol: 🧑‍💻\n---\nYou are a coder.\n")
        assert _ctx._role_symbol("coder") == "🧑‍💻"

    def test_build_agent_context_planner(self, tmp_path, monkeypatch):
        self._setup_context(monkeypatch, tmp_path)
        from orc.coordination.state import BoardStateManager

        board = BoardStateManager(_cfg.get().orc_dir)
        ctx = _ctx.build_agent_context("planner", board=board, agent_id="planner-0")
        assert isinstance(ctx, str)
        assert ".orc/agents/planner/_main.md" in ctx

    def test_build_agent_context_qa_with_feature_branch(self, tmp_path, monkeypatch):
        """QA agent with an active feature branch gets review-specific git info."""
        self._setup_context(monkeypatch, tmp_path)
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), dev_worktree=tmp_path / "dev-wt"))
        monkeypatch.setattr(_cfg.Config, "feature_branch", lambda self, t: "feat/0001-task")
        monkeypatch.setattr(_cfg.Config, "feature_worktree_path", lambda self, t: tmp_path / "feat")
        from orc.coordination.state import BoardStateManager

        ctx = _ctx.build_agent_context(
            "qa",
            board=BoardStateManager(_cfg.get().orc_dir),
            agent_id="qa-0",
            task_name="0001-task.md",
        )
        assert "feat/0001-task" in ctx
        assert "Branch to review" in ctx

    def test_build_agent_context_qa_review_threshold_injected(self, tmp_path, monkeypatch):
        """QA context includes the review threshold when provided."""
        self._setup_context(monkeypatch, tmp_path)
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), dev_worktree=tmp_path / "dev-wt"))
        monkeypatch.setattr(_cfg.Config, "feature_branch", lambda self, t: "feat/0001-task")
        monkeypatch.setattr(_cfg.Config, "feature_worktree_path", lambda self, t: tmp_path / "feat")
        from orc.coordination.state import BoardStateManager
        from orc.squad import ReviewThreshold

        ctx = _ctx.build_agent_context(
            "qa",
            board=BoardStateManager(_cfg.get().orc_dir),
            agent_id="qa-0",
            task_name="0001-task.md",
            review_threshold=ReviewThreshold.HIGH,
        )
        assert "Review threshold: `HIGH`" in ctx
        assert "HIGH" in ctx

    def test_build_agent_context_qa_review_threshold_defaults_to_low(self, tmp_path, monkeypatch):
        """QA context defaults to LOW when no review threshold is provided."""
        self._setup_context(monkeypatch, tmp_path)
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), dev_worktree=tmp_path / "dev-wt"))
        monkeypatch.setattr(_cfg.Config, "feature_branch", lambda self, t: "feat/0001-task")
        monkeypatch.setattr(_cfg.Config, "feature_worktree_path", lambda self, t: tmp_path / "feat")
        from orc.coordination.state import BoardStateManager

        ctx = _ctx.build_agent_context(
            "qa",
            board=BoardStateManager(_cfg.get().orc_dir),
            agent_id="qa-0",
            task_name="0001-task.md",
        )
        assert "Review threshold: `LOW`" in ctx

    def test_build_agent_context_qa_review_threshold_critical(self, tmp_path, monkeypatch):
        """QA context with CRITICAL threshold only fails on critical issues."""
        self._setup_context(monkeypatch, tmp_path)
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), dev_worktree=tmp_path / "dev-wt"))
        monkeypatch.setattr(_cfg.Config, "feature_branch", lambda self, t: "feat/0001-task")
        monkeypatch.setattr(_cfg.Config, "feature_worktree_path", lambda self, t: tmp_path / "feat")
        from orc.coordination.state import BoardStateManager
        from orc.squad import ReviewThreshold

        ctx = _ctx.build_agent_context(
            "qa",
            board=BoardStateManager(_cfg.get().orc_dir),
            agent_id="qa-0",
            task_name="0001-task.md",
            review_threshold=ReviewThreshold.CRITICAL,
        )
        assert "Review threshold: `CRITICAL`" in ctx

    def test_build_context_orc_dir_outside_repo_root(self, tmp_path, monkeypatch):
        """ORC_DIR not under REPO_ROOT → falls back to dir name in role path."""
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        orc_dir = tmp_path / "external-orc"
        orc_dir.mkdir(exist_ok=True)
        (orc_dir / "work").mkdir(exist_ok=True)
        (orc_dir / "work" / "board.yaml").write_text("tasks: []\n")
        agents_dir = orc_dir / "agents"
        agents_dir.mkdir(exist_ok=True)
        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(
                _cfg.get(),
                agents_dir=agents_dir,
                orc_dir=orc_dir,
                repo_root=repo,
                dev_worktree=tmp_path / "dev-wt",
                work_dir=orc_dir / "work",
                board_file=orc_dir / "work" / "board.yaml",
            ),
        )
        monkeypatch.setattr(tg, "_get_messages", lambda limit=100: [])
        monkeypatch.setattr("orc.git.Git.ensure_worktree", lambda self, worktree, branch: None)
        from orc.coordination.state import BoardStateManager

        board = BoardStateManager(_cfg.get().orc_dir)
        ctx = _ctx.build_agent_context("planner", board=board, agent_id="planner-0")
        assert "external-orc/agents/planner/_main.md" in ctx


# ---------------------------------------------------------------------------
# _scan_todos
# ---------------------------------------------------------------------------


class TestScanTodos:
    def _mock_grep(self, monkeypatch, stdout: str, returncode: int = 0) -> None:
        """Mock subprocess.run for grep output."""
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **kw: type("R", (), {"stdout": stdout, "returncode": returncode})(),
        )

    def test_returns_todos_from_git_grep(self, tmp_path, monkeypatch):
        grep_output = "src/foo.py:42:    # TODO: fix this\nsrc/bar.py:7:    # FIXME: broken\n"
        self._mock_grep(monkeypatch, grep_output)
        todos = _ctx._scan_todos(tmp_path)
        assert len(todos) == 2
        assert todos[0] == TodoItem(file="src/foo.py", line=42, tag="TODO", text="# TODO: fix this")
        assert todos[1] == TodoItem(file="src/bar.py", line=7, tag="FIXME", text="# FIXME: broken")

    def test_tags_fixme_correctly(self, tmp_path, monkeypatch):
        grep_output = "a.py:1:    # FIXME: something\n"
        self._mock_grep(monkeypatch, grep_output)
        todos = _ctx._scan_todos(tmp_path)
        assert todos[0].tag == "FIXME"

    def test_returns_empty_on_exception(self, tmp_path, monkeypatch):
        def _raise(*a, **kw):
            raise OSError("no git")

        monkeypatch.setattr(subprocess, "run", _raise)
        assert _ctx._scan_todos(tmp_path) == []

    def test_skips_lines_with_too_few_parts(self, tmp_path, monkeypatch):
        grep_output = "badline\nsrc/ok.py:5:    # TODO: valid\n"
        self._mock_grep(monkeypatch, grep_output)
        todos = _ctx._scan_todos(tmp_path)
        assert len(todos) == 1
        assert todos[0].file == "src/ok.py"

    def test_skips_lines_with_non_int_line_number(self, tmp_path, monkeypatch):
        grep_output = "src/foo.py:notanumber:    # TODO: bad\nsrc/ok.py:3:    # TODO: good\n"
        self._mock_grep(monkeypatch, grep_output)
        todos = _ctx._scan_todos(tmp_path)
        assert len(todos) == 1
        assert todos[0].line == 3

    def test_empty_output_returns_empty(self, tmp_path, monkeypatch):
        self._mock_grep(monkeypatch, "", returncode=1)
        assert _ctx._scan_todos(tmp_path) == []

    def test_docstring_todo_not_included(self, tmp_path, monkeypatch):
        """Lines where TODO/FIXME appears inside a string/docstring are not matched."""
        grep_output = ""
        self._mock_grep(monkeypatch, grep_output, returncode=1)
        todos = _ctx._scan_todos(tmp_path)
        assert todos == []

    def test_real_comment_todo_is_included(self, tmp_path, monkeypatch):
        """Lines where # TODO appears as an actual comment are matched."""
        grep_output = "src/foo.py:10:    # TODO: real action item\n"
        self._mock_grep(monkeypatch, grep_output)
        todos = _ctx._scan_todos(tmp_path)
        assert len(todos) == 1
        assert todos[0].tag == "TODO"
        assert todos[0].text == "# TODO: real action item"

    def test_uses_anchored_regex_pattern(self, tmp_path, monkeypatch):
        """git grep is invoked with ^\\s*#\\s*(TODO|FIXME) to avoid false positives."""
        captured: list[list[str]] = []
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **kw: (
                captured.append(cmd) or type("R", (), {"stdout": "", "returncode": 1})()
            ),
        )
        _ctx._scan_todos(tmp_path)
        assert len(captured) == 1
        cmd = captured[0]
        pattern_index = cmd.index("-E") + 1
        assert cmd[pattern_index].startswith(r"^\s*#")

    def test_exclude_paths_passed_as_pathspecs(self, tmp_path, monkeypatch):
        """orc-todo-scan-exclude entries become :!<path> pathspecs in the git grep command."""
        import orc.config as _cfg

        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(
                _cfg.get(),
                repo_root=tmp_path,
                todo_scan_exclude=(".orc", "vendor"),
            ),
        )
        captured: list[list[str]] = []
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **kw: (
                captured.append(cmd) or type("R", (), {"stdout": "", "returncode": 1})()
            ),
        )
        _ctx._scan_todos(tmp_path)
        assert len(captured) == 1
        cmd = captured[0]
        assert ":!.orc" in cmd
        assert ":!vendor" in cmd


# ---------------------------------------------------------------------------
# _format_todos
# ---------------------------------------------------------------------------


class TestFormatTodos:
    def test_empty_list_returns_placeholder(self):
        result = _ctx._format_todos([])
        assert "_No TODO" in result

    def test_formats_as_markdown_table(self):
        todos = [TodoItem(file="src/x.py", line=10, tag="TODO", text="# TODO: do it")]
        result = _ctx._format_todos(todos)
        assert "| File |" in result
        assert "`src/x.py`" in result
        assert "10" in result
        assert "`TODO`" in result
        assert "# TODO: do it" in result


# ---------------------------------------------------------------------------
# build_agent_context includes todos for planner only
# ---------------------------------------------------------------------------


class TestBuildContextTodos:
    def _setup(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(exist_ok=True)
        work_dir = tmp_path / ".orc" / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "board.yaml").write_text("tasks: []\n")
        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(
                _cfg.get(),
                agents_dir=agents_dir,
                orc_dir=tmp_path / ".orc",
                repo_root=tmp_path,
                dev_worktree=tmp_path / "dev-wt",
                work_dir=work_dir,
                board_file=work_dir / "board.yaml",
            ),
        )
        monkeypatch.setattr(tg, "_get_messages", lambda limit=100: [])
        monkeypatch.setattr("orc.git.Git.ensure_worktree", lambda self, worktree, branch: None)

    def test_planner_context_includes_todos_section(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        fake_todos = [TodoItem(file="x.py", line=5, tag="TODO", text="# TODO: later")]
        monkeypatch.setattr(_ctx, "_scan_todos", lambda root: fake_todos)
        from orc.coordination.state import BoardStateManager

        board = BoardStateManager(_cfg.get().orc_dir)
        ctx = _ctx.build_agent_context("planner", board=board, agent_id="planner-0")
        assert "Code TODOs and FIXMEs" in ctx
        assert "`TODO`" in ctx

    def test_coder_context_excludes_todos_section(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        monkeypatch.setattr(
            _ctx,
            "_scan_todos",
            lambda root: [TodoItem(file="x.py", line=1, tag="TODO", text="x")],
        )
        monkeypatch.setattr(_cfg.Config, "feature_branch", lambda self, t: "feat/0001-x")
        monkeypatch.setattr(_cfg.Config, "feature_worktree_path", lambda self, t: tmp_path / "feat")
        work_dir = tmp_path / ".orc" / "work"
        (work_dir / "board.yaml").write_text(
            "tasks:\n  - name: 0001-task.md\n    assigned_to: null\n"
        )
        from orc.coordination.state import BoardStateManager

        ctx = _ctx.build_agent_context(
            "coder",
            board=BoardStateManager(_cfg.get().orc_dir),
            agent_id="coder-0",
            task_name="0001-task.md",
        )
        assert "Code TODOs and FIXMEs" not in ctx


# ---------------------------------------------------------------------------
# read_work_summary — board-only, no task content, no comments
# ---------------------------------------------------------------------------


class TestReadWorkScoped:
    def test_shows_task_names_and_statuses(self, tmp_path, monkeypatch):
        from orc.coordination.state import BoardStateManager

        work_dir = tmp_path / "work"
        work_dir.mkdir(exist_ok=True)
        (work_dir / "board.yaml").write_text(
            "counter: 2\ntasks:\n"
            "  - name: 0001-a.md\n    status: in-progress\n    assigned_to: coder-1\n"
            "  - name: 0002-b.md\n    status: planned\n"
        )
        (work_dir / "0001-a.md").write_text("Task A content.")
        (work_dir / "0002-b.md").write_text("Task B content.")
        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(
                _cfg.get(),
                orc_dir=tmp_path,
                board_file=work_dir / "board.yaml",
                dev_worktree=tmp_path / "dev-wt",
                work_dir=work_dir,
            ),
        )

        result = BoardStateManager(tmp_path).read_work_summary()
        assert "0001-a.md" in result
        assert "0002-b.md" in result
        assert "in-progress" in result
        assert "planned" in result

    def test_never_includes_task_file_content(self, tmp_path, monkeypatch):
        from orc.coordination.state import BoardStateManager

        work_dir = tmp_path / "work"
        work_dir.mkdir(exist_ok=True)
        (work_dir / "board.yaml").write_text("counter: 1\ntasks:\n  - name: 0001-a.md\n")
        (work_dir / "0001-a.md").write_text("Task A content.")
        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(
                _cfg.get(),
                orc_dir=tmp_path,
                board_file=work_dir / "board.yaml",
                dev_worktree=tmp_path / "dev-wt",
                work_dir=work_dir,
            ),
        )

        result = BoardStateManager(tmp_path).read_work_summary()
        assert "Task A content." not in result
        assert "_(summary only)_" not in result

    def test_never_includes_comments(self, tmp_path, monkeypatch):
        from orc.coordination.state import BoardStateManager

        work_dir = tmp_path / "work"
        work_dir.mkdir(exist_ok=True)
        (work_dir / "board.yaml").write_text(
            "counter: 1\ntasks:\n"
            "  - name: 0001-a.md\n    status: in-progress\n"
            "    comments:\n"
            "      - from: qa-1\n        text: fix the tests\n        ts: '2024-01-01T00:00:00Z'\n"
        )
        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(
                _cfg.get(),
                orc_dir=tmp_path,
                board_file=work_dir / "board.yaml",
                dev_worktree=tmp_path / "dev-wt",
                work_dir=work_dir,
            ),
        )

        result = BoardStateManager(tmp_path).read_work_summary()
        assert "fix the tests" not in result
        assert "comments" not in result
