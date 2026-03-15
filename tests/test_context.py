"""Tests for orc/context.py."""

import subprocess
import time
from dataclasses import replace as _replace

import pytest
from conftest import make_msg

import orc.config as _cfg
import orc.engine.context as _ctx
import orc.git.core as _git
import orc.messaging.telegram as tg

# ---------------------------------------------------------------------------
# _boot_message_body
# ---------------------------------------------------------------------------


class TestBootMessageBody:
    def _write_board(self, content: str) -> None:
        board = _cfg.get().work_dir / "board.yaml"
        board.parent.mkdir(parents=True, exist_ok=True)
        board.write_text(content)

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
            (
                "planner-1",
                "counter: 2\ntasks: []\nvisions:\n  - vision.md\n",
                "translating vision docs.",
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
        assert _ctx._boot_message_body(agent_id) == expected


# ---------------------------------------------------------------------------
# wait_for_human_reply
# ---------------------------------------------------------------------------


class TestWaitForHumanReply:
    def _human(self, text: str, ts: int) -> dict:
        return {"text": text, "date": ts, "from": {"username": "pietro", "first_name": "Pietro"}}

    def _patch_configured(self, monkeypatch) -> None:
        monkeypatch.setattr(tg, "is_configured", lambda: True)

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
        monkeypatch.setattr(tg, "get_messages", lambda: snapshot + [human])
        self._mock_time(monkeypatch, [0.0, 1.0])

        result = _ctx.wait_for_human_reply(snapshot, timeout=3600.0)
        assert result == "Here is the clarification."

    def test_skips_snapshot_messages(self, monkeypatch):
        self._patch_configured(monkeypatch)
        old_human = self._human("old message", ts=500)
        snapshot = [old_human]
        new_human = self._human("new message", ts=600)
        monkeypatch.setattr(tg, "get_messages", lambda: snapshot + [new_human])
        self._mock_time(monkeypatch, [0.0, 1.0])

        result = _ctx.wait_for_human_reply(snapshot, timeout=3600.0)
        assert result == "new message"

    def test_skips_agent_messages(self, monkeypatch):
        self._patch_configured(monkeypatch)
        snapshot = [make_msg("[coder-1](blocked) 2026-03-09T11:00:00Z: Blocked.", ts=1000)]
        agent_msg = make_msg("[planner-1](ready) 2026-03-09T11:30:00Z: ADR updated.", ts=2000)
        human_msg = self._human("Please continue.", ts=3000)
        call_count = 0

        def get_messages():
            nonlocal call_count
            call_count += 1
            return snapshot + [agent_msg] if call_count == 1 else snapshot + [agent_msg, human_msg]

        monkeypatch.setattr(tg, "get_messages", get_messages)
        sleeps = self._mock_time(monkeypatch, [0.0, 1.0, 2.0])

        result = _ctx.wait_for_human_reply(snapshot, initial_delay=5.0, timeout=3600.0)
        assert result == "Please continue."
        assert len(sleeps) == 2

    def test_exponential_backoff(self, monkeypatch):
        self._patch_configured(monkeypatch)
        snapshot: list[dict] = []
        human = self._human("Done.", ts=9000)
        call_count = 0

        def get_messages():
            nonlocal call_count
            call_count += 1
            return snapshot if call_count < 3 else [human]

        monkeypatch.setattr(tg, "get_messages", get_messages)
        sleeps = self._mock_time(monkeypatch, [0.0, 1.0, 2.0, 3.0])

        _ctx.wait_for_human_reply(
            snapshot, initial_delay=5.0, backoff_factor=2.0, max_delay=300.0, timeout=3600.0
        )
        assert sleeps == [5.0, 10.0, 20.0]

    def test_backoff_capped_at_max_delay(self, monkeypatch):
        self._patch_configured(monkeypatch)
        snapshot: list[dict] = []
        human = self._human("Done.", ts=9000)
        call_count = 0

        def get_messages():
            nonlocal call_count
            call_count += 1
            return snapshot if call_count < 4 else [human]

        monkeypatch.setattr(tg, "get_messages", get_messages)
        sleeps = self._mock_time(monkeypatch, [0.0, 1.0, 2.0, 3.0, 4.0])

        _ctx.wait_for_human_reply(
            snapshot, initial_delay=5.0, backoff_factor=2.0, max_delay=10.0, timeout=3600.0
        )
        assert sleeps == [5.0, 10.0, 10.0, 10.0]

    def test_raises_timeout_error(self, monkeypatch):
        import pytest

        self._patch_configured(monkeypatch)
        snapshot: list[dict] = []
        monkeypatch.setattr(tg, "get_messages", lambda: snapshot)
        self._mock_time(monkeypatch, [0.0, 3601.0])

        with pytest.raises(TimeoutError):
            _ctx.wait_for_human_reply(snapshot, timeout=3600.0)

    def test_sleep_trimmed_to_deadline(self, monkeypatch):
        """Sleep must not overshoot the deadline."""
        import pytest

        self._patch_configured(monkeypatch)
        snapshot: list[dict] = []
        monkeypatch.setattr(tg, "get_messages", lambda: snapshot)
        sleeps = self._mock_time(monkeypatch, [0.0, 9.0, 10.1])

        with pytest.raises(TimeoutError):
            _ctx.wait_for_human_reply(snapshot, initial_delay=300.0, timeout=10.0)

        assert sleeps == [1.0]

    def test_not_configured_raises_timeout_immediately(self, monkeypatch):
        """Without Telegram, wait_for_human_reply raises TimeoutError immediately."""
        import pytest

        monkeypatch.setattr(tg, "is_configured", lambda: False)
        with pytest.raises(TimeoutError, match="not configured"):
            _ctx.wait_for_human_reply([], timeout=3600.0)


# ---------------------------------------------------------------------------
# Coverage tests for context.py helpers
# ---------------------------------------------------------------------------


class TestContextCoverage:
    def _setup_context(self, monkeypatch, tmp_path, *, roles_dir=None, board_content="tasks: []\n"):
        """Set up full context with config, directories, and mocks."""
        if roles_dir is None:
            roles_dir = tmp_path / "roles"
            roles_dir.mkdir(exist_ok=True)
        work_dir = tmp_path / ".orc" / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "board.yaml").write_text(board_content)
        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(
                _cfg.get(),
                roles_dir=roles_dir,
                orc_dir=tmp_path / ".orc",
                repo_root=tmp_path,
                work_dir=work_dir,
                board_file=work_dir / "board.yaml",
            ),
        )
        monkeypatch.setattr(tg, "get_messages", lambda: [])
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)

    def _setup_roles(self, monkeypatch, tmp_path):
        """Set up minimal roles configuration."""
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir(exist_ok=True)
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), roles_dir=roles_dir))
        monkeypatch.setattr(_cfg, "_PACKAGE_ROLES_DIR", tmp_path / "pkg_roles")
        return roles_dir

    def test_read_adrs_empty_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            _cfg, "_config", _replace(_cfg.get(), orc_dir=tmp_path / ".orc", repo_root=tmp_path)
        )
        result = _ctx._read_adrs()
        assert result == "_No ADRs found._"

    def test_read_adrs_with_files(self, tmp_path, monkeypatch):
        adr_dir = tmp_path / "docs" / "adr"
        adr_dir.mkdir(parents=True, exist_ok=True)
        (adr_dir / "001-decision.md").write_text("# ADR 001\n\nSome decision.")
        (adr_dir / "README.md").write_text("# ADRs index")
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), repo_root=tmp_path))
        result = _ctx._read_adrs()
        assert "001-decision.md" in result
        assert "README.md" not in result

    def test_parse_role_file_missing_returns_default(self, tmp_path, monkeypatch):
        self._setup_roles(monkeypatch, tmp_path)
        result = _ctx._parse_role_file("wizard")
        assert "wizard" in result

    def test_parse_role_file_directory_format(self, tmp_path, monkeypatch):
        """Directory format: _main.md loaded first, then remaining files alphabetically."""
        roles_dir = self._setup_roles(monkeypatch, tmp_path)
        role_dir = roles_dir / "coder"
        role_dir.mkdir(parents=True, exist_ok=True)
        (role_dir / "_main.md").write_text("---\nsymbol: 🛠️\n---\nIdentity section.")
        (role_dir / "constraints.md").write_text("Constraints section.")
        (role_dir / "exit-states.md").write_text("Exit states section.")
        result = _ctx._parse_role_file("coder")
        assert "Identity section." in result
        assert "Constraints section." in result
        assert "Exit states section." in result
        assert "symbol" not in result
        assert result.index("Identity section.") < result.index("Constraints section.")
        assert result.index("Constraints section.") < result.index("Exit states section.")

    def test_parse_role_file_directory_takes_precedence_over_flat_file(self, tmp_path, monkeypatch):
        """When both directory and .md file exist, directory wins."""
        roles_dir = self._setup_roles(monkeypatch, tmp_path)
        role_dir = roles_dir / "coder"
        role_dir.mkdir(parents=True, exist_ok=True)
        (role_dir / "_main.md").write_text("Directory version.")
        (roles_dir / "coder.md").write_text("Flat file version.")
        result = _ctx._parse_role_file("coder")
        assert "Directory version." in result
        assert "Flat file version." not in result

    def test_parse_role_dir_empty_returns_fallback(self, tmp_path, monkeypatch):
        """Empty directory returns a fallback string."""
        roles_dir = self._setup_roles(monkeypatch, tmp_path)
        role_dir = roles_dir / "coder"
        role_dir.mkdir(parents=True, exist_ok=True)
        result = _ctx._parse_role_file("coder")
        assert "coder" in result

    def test_parse_role_file_project_dir_overrides_pkg_flat(self, tmp_path, monkeypatch):
        """Project-level directory overrides package-level flat file."""
        roles_dir = self._setup_roles(monkeypatch, tmp_path)
        role_dir = roles_dir / "coder"
        role_dir.mkdir(parents=True, exist_ok=True)
        (role_dir / "_main.md").write_text("Project directory version.")
        pkg_roles = tmp_path / "pkg_roles"
        pkg_roles.mkdir(exist_ok=True)
        (pkg_roles / "coder.md").write_text("Package flat version.")
        monkeypatch.setattr(_cfg, "_PACKAGE_ROLES_DIR", pkg_roles)
        result = _ctx._parse_role_file("coder")
        assert "Project directory version." in result
        assert "Package flat version." not in result

    def test_role_symbol_directory_format(self, tmp_path, monkeypatch):
        """_role_symbol reads from _main.md when role is a directory."""
        roles_dir = self._setup_roles(monkeypatch, tmp_path)
        role_dir = roles_dir / "coder"
        role_dir.mkdir(parents=True, exist_ok=True)
        (role_dir / "_main.md").write_text("---\nsymbol: 🛠️\n---\nYou are a coder.\n")
        assert _ctx._role_symbol("coder") == "🛠️"

    def test_parse_role_file_with_frontmatter(self, tmp_path, monkeypatch):
        roles_dir = self._setup_roles(monkeypatch, tmp_path)
        (roles_dir / "coder.md").write_text("---\nsymbol: 🧑‍💻\n---\nYou are the coder agent.")
        result = _ctx._parse_role_file("coder")
        assert "coder agent" in result
        assert "symbol" not in result

    def test_role_symbol_returns_empty_when_no_file(self, tmp_path, monkeypatch):
        self._setup_roles(monkeypatch, tmp_path)
        assert _ctx._role_symbol("wizard") == ""

    def test_role_symbol_no_frontmatter(self, tmp_path, monkeypatch):
        roles_dir = self._setup_roles(monkeypatch, tmp_path)
        (roles_dir / "coder.md").write_text("You are the coder.")
        assert _ctx._role_symbol("coder") == ""

    def test_role_symbol_frontmatter_no_end(self, tmp_path, monkeypatch):
        """Frontmatter with no closing --- → symbol not extracted."""
        roles_dir = self._setup_roles(monkeypatch, tmp_path)
        (roles_dir / "coder.md").write_text("---\nsymbol: 🧑‍💻\nno closing marker")
        assert _ctx._role_symbol("coder") == ""

    def test_build_agent_context_planner(self, tmp_path, monkeypatch):
        self._setup_context(monkeypatch, tmp_path)
        model, ctx = _ctx.build_agent_context("planner", [], worktree=tmp_path)
        assert isinstance(ctx, str)
        assert len(ctx) > 0

    def test_build_agent_context_qa_with_feature_branch(self, tmp_path, monkeypatch):
        """QA agent with an active feature branch gets review-specific git info."""
        self._setup_context(monkeypatch, tmp_path)
        work_dir = tmp_path / ".orc" / "work"
        (work_dir / "board.yaml").write_text(
            "tasks:\n  - name: 0001-task.md\n    assigned_to: null\n"
        )
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), dev_worktree=tmp_path / "dev-wt"))
        monkeypatch.setattr(_git, "_feature_branch", lambda t: "feat/0001-task")
        monkeypatch.setattr(_git, "_feature_worktree_path", lambda t: tmp_path / "feat")
        _, ctx = _ctx.build_agent_context("qa", [], worktree=tmp_path)
        assert "feat/0001-task" in ctx
        assert "Branch to review" in ctx

    def test_role_symbol_with_symbol_in_frontmatter(self, tmp_path, monkeypatch):
        """Lines 65-67: role file has valid frontmatter containing 'symbol' key."""
        roles_dir = self._setup_roles(monkeypatch, tmp_path)
        (roles_dir / "coder.md").write_text("---\nsymbol: 🧑‍💻\n---\nYou are a coder.\n")
        assert _ctx._role_symbol("coder") == "🧑‍💻"

    def test_build_context_planner_with_feature_branch(self, tmp_path, monkeypatch):
        """Line 136: else-branch with feature_branch set (agent_name not coder/qa)."""
        self._setup_context(monkeypatch, tmp_path)
        work_dir = tmp_path / ".orc" / "work"
        (work_dir / "board.yaml").write_text(
            "tasks:\n  - name: 0001-task.md\n    assigned_to: null\n"
        )
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), dev_worktree=tmp_path / "dev-wt"))
        monkeypatch.setattr(_git, "_feature_branch", lambda t: "feature/0001-task")
        monkeypatch.setattr(_git, "_feature_worktree_path", lambda t: tmp_path / "feat")
        _, ctx = _ctx.build_agent_context("planner", [], worktree=tmp_path)
        assert "feature/0001-task" in ctx

    def test_build_context_orc_dir_outside_repo_root(self, tmp_path, monkeypatch):
        """Lines 86-87: ORC_DIR not under REPO_ROOT → falls back to dir name."""
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        orc_dir = tmp_path / "external-orc"
        orc_dir.mkdir(exist_ok=True)
        (orc_dir / "work").mkdir(exist_ok=True)
        (orc_dir / "work" / "board.yaml").write_text("tasks: []\n")
        roles_dir = orc_dir / "roles"
        roles_dir.mkdir(exist_ok=True)
        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(
                _cfg.get(),
                roles_dir=roles_dir,
                orc_dir=orc_dir,
                repo_root=repo,
                dev_worktree=tmp_path / "dev-wt",
                work_dir=orc_dir / "work",
                board_file=orc_dir / "work" / "board.yaml",
            ),
        )
        monkeypatch.setattr(tg, "get_messages", lambda: [])
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: repo)
        _, ctx = _ctx.build_agent_context("planner", [])
        assert "external-orc/work/" in ctx


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
        assert todos[0] == {
            "file": "src/foo.py",
            "line": 42,
            "tag": "TODO",
            "text": "# TODO: fix this",
        }
        assert todos[1] == {
            "file": "src/bar.py",
            "line": 7,
            "tag": "FIXME",
            "text": "# FIXME: broken",
        }

    def test_tags_fixme_correctly(self, tmp_path, monkeypatch):
        grep_output = "a.py:1:    # FIXME: something\n"
        self._mock_grep(monkeypatch, grep_output)
        todos = _ctx._scan_todos(tmp_path)
        assert todos[0]["tag"] == "FIXME"

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
        assert todos[0]["file"] == "src/ok.py"

    def test_skips_lines_with_non_int_line_number(self, tmp_path, monkeypatch):
        grep_output = "src/foo.py:notanumber:    # TODO: bad\nsrc/ok.py:3:    # TODO: good\n"
        self._mock_grep(monkeypatch, grep_output)
        todos = _ctx._scan_todos(tmp_path)
        assert len(todos) == 1
        assert todos[0]["line"] == 3

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
        assert todos[0]["tag"] == "TODO"
        assert todos[0]["text"] == "# TODO: real action item"

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
        todos = [{"file": "src/x.py", "line": 10, "tag": "TODO", "text": "# TODO: do it"}]
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
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir(exist_ok=True)
        work_dir = tmp_path / ".orc" / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "board.yaml").write_text("tasks: []\n")
        monkeypatch.setattr(
            _cfg,
            "_config",
            _replace(
                _cfg.get(),
                roles_dir=roles_dir,
                orc_dir=tmp_path / ".orc",
                repo_root=tmp_path,
                dev_worktree=tmp_path / "dev-wt",
                work_dir=work_dir,
                board_file=work_dir / "board.yaml",
            ),
        )
        monkeypatch.setattr(tg, "get_messages", lambda: [])
        monkeypatch.setattr(_git, "_ensure_dev_worktree", lambda: tmp_path)

    def test_planner_context_includes_todos_section(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        fake_todos = [{"file": "x.py", "line": 5, "tag": "TODO", "text": "# TODO: later"}]
        monkeypatch.setattr(_ctx, "_scan_todos", lambda root: fake_todos)
        _, ctx = _ctx.build_agent_context("planner", [])
        assert "Code TODOs and FIXMEs" in ctx
        assert "`TODO`" in ctx

    def test_coder_context_excludes_todos_section(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        monkeypatch.setattr(
            _ctx,
            "_scan_todos",
            lambda root: [{"file": "x.py", "line": 1, "tag": "TODO", "text": "x"}],
        )
        monkeypatch.setattr(_git, "_feature_branch", lambda t: "feat/0001-x")
        monkeypatch.setattr(_git, "_feature_worktree_path", lambda t: tmp_path / "feat")
        work_dir = tmp_path / ".orc" / "work"
        (work_dir / "board.yaml").write_text(
            "tasks:\n  - name: 0001-task.md\n    assigned_to: null\n"
        )
        _, ctx = _ctx.build_agent_context("coder", [])
        assert "Code TODOs and FIXMEs" not in ctx


# ---------------------------------------------------------------------------
# _summarize_adr / _read_adrs(summarize=True)
# ---------------------------------------------------------------------------


class TestAdrSummarize:
    def test_summarize_adr_extracts_title_and_status(self, tmp_path):
        adr = tmp_path / "0001-test.md"
        adr.write_text(
            "# ADR-0001 — My Decision\n\n"
            "**Status:** Accepted\n\n---\n\n"
            "## Context\n\n"
            "We need to decide on a database.\n\n"
            "## Decision\n\nUse PostgreSQL.\n"
        )
        result = _ctx._summarize_adr(adr)
        assert "ADR-0001 — My Decision" in result
        assert "**Status:** Accepted" in result
        assert "We need to decide on a database." in result
        assert "Full text:" in result
        # Should NOT include full body
        assert "Use PostgreSQL" not in result

    def test_summarize_adr_no_status(self, tmp_path):
        adr = tmp_path / "0002-simple.md"
        adr.write_text("# Simple ADR\n\nJust a paragraph of context.\n")
        result = _ctx._summarize_adr(adr)
        assert "Simple ADR" in result
        assert "Just a paragraph of context." in result

    def test_summarize_adr_stops_at_next_heading_after_paragraph(self, tmp_path):
        adr = tmp_path / "0003-heading.md"
        adr.write_text(
            "# ADR 003\n\n**Status:** Accepted\n\n"
            "First paragraph line.\n"
            "## Decision\n\nShould not appear.\n"
        )
        result = _ctx._summarize_adr(adr)
        assert "First paragraph line." in result
        assert "Should not appear" not in result

    def test_read_adrs_summarize_mode(self, tmp_path, monkeypatch):
        adr_dir = tmp_path / "docs" / "adr"
        adr_dir.mkdir(parents=True, exist_ok=True)
        (adr_dir / "0001-first.md").write_text(
            "# ADR 001\n\n**Status:** Accepted\n\n---\n\n"
            "## Context\n\nSome decision context.\n\n"
            "## Decision\n\nLots of detail here that should not appear.\n"
        )
        (adr_dir / "README.md").write_text("# Index")
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), repo_root=tmp_path))
        result = _ctx._read_adrs(summarize=True)
        assert "ADR 001" in result
        assert "Full text:" in result
        assert "Lots of detail" not in result

    def test_read_adrs_full_mode(self, tmp_path, monkeypatch):
        adr_dir = tmp_path / "docs" / "adr"
        adr_dir.mkdir(parents=True, exist_ok=True)
        (adr_dir / "0001-first.md").write_text("# ADR 001\n\nFull body text.\n")
        monkeypatch.setattr(_cfg, "_config", _replace(_cfg.get(), repo_root=tmp_path))
        result = _ctx._read_adrs(summarize=False)
        assert "Full body text." in result
        assert "Full text:" not in result


# ---------------------------------------------------------------------------
# _extract_readme / _extract_contributing / _keep_sections
# ---------------------------------------------------------------------------


class TestDocExtraction:
    _SAMPLE_README = (
        "# My Project\n\nProject description.\n\n"
        "## How it works\n\nIt does things.\n\n"
        "## Installation\n\n```bash\npip install myproj\n```\n\n"
        "## Quick start\n\nRun the thing.\n\n"
        "## Architecture\n\nModular design.\n"
    )

    _SAMPLE_CONTRIBUTING = (
        "# Contributing\n\nWelcome.\n\n"
        "## First-time setup\n\nClone and install.\n\n"
        "## The development loop (TDD)\n\nWrite tests first.\n\n"
        "## Committing\n\nUse conventional commits.\n\n"
        "## Package layout\n\nsrc/myproj/...\n\n"
        "## Writing an ADR\n\nFollow the template.\n"
    )

    def test_extract_readme_strips_install_sections(self):
        result = _ctx._extract_readme(self._SAMPLE_README)
        assert "Project description." in result
        assert "It does things." in result
        assert "Modular design." in result
        assert "pip install" not in result
        assert "Run the thing" not in result

    def test_extract_contributing_for_coder(self):
        result = _ctx._extract_contributing(self._SAMPLE_CONTRIBUTING, "coder")
        assert "Write tests first" in result
        assert "Use conventional commits" in result
        assert "src/myproj" in result
        # Should NOT include setup or ADR writing
        assert "Clone and install" not in result
        assert "Follow the template" not in result

    def test_extract_contributing_for_planner(self):
        result = _ctx._extract_contributing(self._SAMPLE_CONTRIBUTING, "planner")
        assert "src/myproj" in result
        assert "Follow the template" in result
        # Should NOT include TDD or committing
        assert "Write tests first" not in result
        assert "Use conventional commits" not in result

    def test_extract_contributing_for_qa(self):
        result = _ctx._extract_contributing(self._SAMPLE_CONTRIBUTING, "qa")
        assert "Write tests first" in result
        assert "Use conventional commits" in result
        assert "src/myproj" in result
        assert "Clone and install" not in result

    def test_extract_contributing_unknown_role_returns_full(self):
        result = _ctx._extract_contributing(self._SAMPLE_CONTRIBUTING, "unknown_role")
        assert "Clone and install" in result
        assert "Write tests first" in result

    def test_keep_sections_skip_mode(self):
        text = "# Title\n\nIntro.\n\n## Keep\n\nGood.\n\n## Drop\n\nBad.\n"
        result = _ctx._keep_sections(text, skip=frozenset({"drop"}))
        assert "Good." in result
        assert "Bad." not in result
        assert "Intro." in result

    def test_keep_sections_keep_mode(self):
        text = "# Title\n\nPreamble.\n\n## Alpha\n\nA content.\n\n## Beta\n\nB content.\n"
        result = _ctx._keep_sections(text, keep=frozenset({"alpha"}))
        assert "Preamble." in result
        assert "A content." in result
        assert "B content." not in result


# ---------------------------------------------------------------------------
# _window_chat
# ---------------------------------------------------------------------------


class TestWindowChat:
    def test_empty_chat_unchanged(self):
        assert _ctx._window_chat("") == ""

    def test_short_chat_unchanged(self):
        text = "\n".join(f"line {i}" for i in range(10))
        assert _ctx._window_chat(text, max_recent=50) == text

    def test_long_chat_trims_old_non_agent_lines(self):
        old_lines = [f"human message {i}" for i in range(20)]
        agent_line = "[coder-1](done) 2026-03-01T12:00:00Z: Task complete."
        old_lines.insert(5, agent_line)
        recent_lines = [f"recent line {i}" for i in range(10)]
        text = "\n".join(old_lines + recent_lines)
        result = _ctx._window_chat(text, max_recent=10)
        # Recent lines preserved
        assert "recent line 0" in result
        assert "recent line 9" in result
        # Agent state line preserved from old section
        assert "[coder-1](done)" in result
        # Old human messages trimmed
        assert "human message 0" not in result
        assert "older messages trimmed" in result

    def test_all_old_are_agent_lines(self):
        old_lines = [f"[agent-{i}](running) msg" for i in range(5)]
        recent_lines = [f"recent {i}" for i in range(3)]
        text = "\n".join(old_lines + recent_lines)
        result = _ctx._window_chat(text, max_recent=3)
        # All agent lines kept
        for i in range(5):
            assert f"[agent-{i}](running)" in result
        # No trimmed notice since nothing was dropped
        assert "trimmed" not in result


# ---------------------------------------------------------------------------
# _read_work(active_only=...)
# ---------------------------------------------------------------------------


class TestReadWorkScoped:
    def test_active_only_includes_only_target_task(self, tmp_path, monkeypatch):
        work_dir = tmp_path / "work"
        work_dir.mkdir(exist_ok=True)
        (work_dir / "board.yaml").write_text("tasks:\n  - name: 0001-a.md\n  - name: 0002-b.md\n")
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

        import orc.coordination.board as _board

        result = _board._read_work(active_only="0001-a.md")
        assert "Task A content." in result
        assert "Task B content." not in result
        assert "0002-b.md" in result  # name still listed
        assert "_(summary only)_" in result

    def test_no_active_only_includes_all(self, tmp_path, monkeypatch):
        work_dir = tmp_path / "work"
        work_dir.mkdir(exist_ok=True)
        (work_dir / "board.yaml").write_text("tasks:\n  - name: 0001-a.md\n")
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

        import orc.coordination.board as _board

        result = _board._read_work()
        assert "Task A content." in result
        assert "_(summary only)_" not in result
