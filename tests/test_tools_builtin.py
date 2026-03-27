"""Tests for :mod:`orc.ai.tools` — built-in tool system."""

from __future__ import annotations

from pathlib import Path

import pytest

from orc.ai.llm import ToolCall
from orc.ai.tools import (
    PermissionChecker,
    ToolExecutor,
    get_tool_definitions,
)
from orc.squad import AgentRole, PermissionConfig

# ---------------------------------------------------------------------------
# PermissionChecker
# ---------------------------------------------------------------------------


class TestPermissionChecker:
    def test_yolo_allows_everything(self) -> None:
        checker = PermissionChecker(PermissionConfig(mode="yolo"))
        assert checker.is_allowed("read_file", {"path": "/etc/passwd"})
        assert checker.is_allowed("shell", {"command": "rm -rf /"})

    def test_confined_allows_read(self) -> None:
        checker = PermissionChecker(PermissionConfig(mode="confined", allow_tools=("read",)))
        assert checker.is_allowed("read_file", {"path": "test.py"})
        assert checker.is_allowed("list_directory", {"path": "."})

    def test_confined_denies_write_without_permission(self) -> None:
        checker = PermissionChecker(PermissionConfig(mode="confined", allow_tools=("read",)))
        assert not checker.is_allowed("write_file", {"path": "x", "content": "y"})

    def test_confined_denies_shell_without_pattern(self) -> None:
        checker = PermissionChecker(
            PermissionConfig(mode="confined", allow_tools=("read", "write"))
        )
        assert not checker.is_allowed("shell", {"command": "ls"})

    def test_shell_pattern_match(self) -> None:
        checker = PermissionChecker(
            PermissionConfig(mode="confined", allow_tools=("shell(git:*)",))
        )
        assert checker.is_allowed("shell", {"command": "git status"})
        assert checker.is_allowed("shell", {"command": "git log --oneline"})
        assert not checker.is_allowed("shell", {"command": "rm -rf ."})

    def test_shell_deny_overrides_allow(self) -> None:
        checker = PermissionChecker(
            PermissionConfig(
                mode="confined",
                allow_tools=("shell(git:*)",),
                deny_tools=("shell(git push:*)",),
            )
        )
        assert checker.is_allowed("shell", {"command": "git status"})
        assert not checker.is_allowed("shell", {"command": "git push origin main"})

    def test_orc_tools_need_orc_permission(self) -> None:
        checker = PermissionChecker(PermissionConfig(mode="confined", allow_tools=("orc",)))
        assert checker.is_allowed("get_task", {"task_filename": "0001-test.md"})
        assert checker.is_allowed("update_task_status", {"task_code": "0001", "status": "done"})

    def test_unknown_tool_denied(self) -> None:
        checker = PermissionChecker(
            PermissionConfig(mode="confined", allow_tools=("read", "write", "orc"))
        )
        assert not checker.is_allowed("unknown_tool", {})

    def test_deny_overrides_allow_for_categories(self) -> None:
        checker = PermissionChecker(
            PermissionConfig(mode="confined", allow_tools=("read", "write"), deny_tools=("write",))
        )
        assert checker.is_allowed("read_file", {"path": "test.py"})
        assert not checker.is_allowed("write_file", {"path": "x", "content": "y"})

    def test_compound_command_all_allowed(self) -> None:
        checker = PermissionChecker(
            PermissionConfig(
                mode="confined",
                allow_tools=("shell(git:*)", "shell(pytest:*)"),
            )
        )
        assert checker.is_allowed("shell", {"command": "git status && pytest tests/"})

    def test_compound_command_one_denied(self) -> None:
        checker = PermissionChecker(
            PermissionConfig(mode="confined", allow_tools=("shell(git:*)",))
        )
        assert not checker.is_allowed("shell", {"command": "git status && rm -rf ."})

    def test_compound_cd_prefix_stripped(self) -> None:
        checker = PermissionChecker(
            PermissionConfig(mode="confined", allow_tools=("shell(pytest:*)",))
        )
        assert checker.is_allowed("shell", {"command": "cd /some/path && pytest tests/"})

    def test_compound_semicolon_split(self) -> None:
        checker = PermissionChecker(
            PermissionConfig(
                mode="confined",
                allow_tools=("shell(git:*)", "shell(echo:*)"),
            )
        )
        assert checker.is_allowed("shell", {"command": "git add .; echo done"})


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


class TestGetToolDefinitions:
    def test_coder_gets_close_task(self) -> None:
        defs = get_tool_definitions(AgentRole.CODER)
        names = {d["function"]["name"] for d in defs}
        assert "close_task" in names
        assert "review_task" not in names
        assert "close_merge" not in names

    def test_qa_gets_review_task(self) -> None:
        defs = get_tool_definitions(AgentRole.QA)
        names = {d["function"]["name"] for d in defs}
        assert "review_task" in names
        assert "close_task" not in names

    def test_planner_gets_create_task(self) -> None:
        defs = get_tool_definitions(AgentRole.PLANNER)
        names = {d["function"]["name"] for d in defs}
        assert "create_task" in names
        assert "get_vision" in names
        assert "close_vision" in names

    def test_merger_gets_close_merge(self) -> None:
        defs = get_tool_definitions(AgentRole.MERGER)
        names = {d["function"]["name"] for d in defs}
        assert "close_merge" in names
        assert "close_task" not in names

    def test_all_roles_get_shared_tools(self) -> None:
        for role in AgentRole:
            defs = get_tool_definitions(role)
            names = {d["function"]["name"] for d in defs}
            assert "read_file" in names
            assert "write_file" in names
            assert "shell" in names
            assert "get_task" in names
            assert "update_task_status" in names
            assert "add_comment" in names


# ---------------------------------------------------------------------------
# ToolExecutor — file operations
# ---------------------------------------------------------------------------


class TestToolExecutorFiles:
    @pytest.fixture()
    def executor(self, tmp_path: Path) -> ToolExecutor:
        return ToolExecutor(
            cwd=tmp_path,
            role=AgentRole.CODER,
            permissions=PermissionConfig(mode="yolo"),
        )

    def test_read_file(self, executor: ToolExecutor, tmp_path: Path) -> None:
        (tmp_path / "test.txt").write_text("hello\nworld\n")
        result = executor.execute(
            ToolCall(id="1", name="read_file", arguments='{"path": "test.txt"}')
        )
        assert "hello" in result
        assert "world" in result
        assert "1 |" in result  # line numbers

    def test_read_nonexistent_file(self, executor: ToolExecutor) -> None:
        result = executor.execute(
            ToolCall(id="1", name="read_file", arguments='{"path": "nope.txt"}')
        )
        assert "Error" in result

    def test_write_file(self, executor: ToolExecutor, tmp_path: Path) -> None:
        result = executor.execute(
            ToolCall(
                id="1",
                name="write_file",
                arguments='{"path": "new.txt", "content": "hello world"}',
            )
        )
        assert "Wrote" in result
        assert (tmp_path / "new.txt").read_text() == "hello world"

    def test_write_file_creates_dirs(self, executor: ToolExecutor, tmp_path: Path) -> None:
        executor.execute(
            ToolCall(
                id="1",
                name="write_file",
                arguments='{"path": "sub/dir/file.txt", "content": "nested"}',
            )
        )
        assert (tmp_path / "sub" / "dir" / "file.txt").read_text() == "nested"

    def test_edit_file(self, executor: ToolExecutor, tmp_path: Path) -> None:
        (tmp_path / "edit.txt").write_text("foo bar baz")
        result = executor.execute(
            ToolCall(
                id="1",
                name="edit_file",
                arguments='{"path": "edit.txt", "old_str": "bar", "new_str": "BAR"}',
            )
        )
        assert "replaced 1" in result
        assert (tmp_path / "edit.txt").read_text() == "foo BAR baz"

    def test_edit_file_not_found(self, executor: ToolExecutor) -> None:
        result = executor.execute(
            ToolCall(
                id="1",
                name="edit_file",
                arguments='{"path": "nope.txt", "old_str": "x", "new_str": "y"}',
            )
        )
        assert "Error" in result

    def test_edit_file_no_match(self, executor: ToolExecutor, tmp_path: Path) -> None:
        (tmp_path / "edit.txt").write_text("hello")
        result = executor.execute(
            ToolCall(
                id="1",
                name="edit_file",
                arguments='{"path": "edit.txt", "old_str": "notfound", "new_str": "x"}',
            )
        )
        assert "not found" in result

    def test_edit_file_ambiguous(self, executor: ToolExecutor, tmp_path: Path) -> None:
        (tmp_path / "edit.txt").write_text("aaa aaa")
        result = executor.execute(
            ToolCall(
                id="1",
                name="edit_file",
                arguments='{"path": "edit.txt", "old_str": "aaa", "new_str": "bbb"}',
            )
        )
        assert "2 times" in result

    def test_list_directory(self, executor: ToolExecutor, tmp_path: Path) -> None:
        (tmp_path / "a.py").touch()
        (tmp_path / "b.txt").touch()
        (tmp_path / "subdir").mkdir()
        result = executor.execute(
            ToolCall(id="1", name="list_directory", arguments='{"path": "."}')
        )
        assert "a.py" in result
        assert "b.txt" in result
        assert "subdir/" in result

    def test_list_directory_hides_dotfiles(self, executor: ToolExecutor, tmp_path: Path) -> None:
        (tmp_path / ".hidden").touch()
        (tmp_path / "visible").touch()
        result = executor.execute(
            ToolCall(id="1", name="list_directory", arguments='{"path": "."}')
        )
        assert ".hidden" not in result
        assert "visible" in result


# ---------------------------------------------------------------------------
# ToolExecutor — shell
# ---------------------------------------------------------------------------


class TestToolExecutorShell:
    @pytest.fixture()
    def executor(self, tmp_path: Path) -> ToolExecutor:
        return ToolExecutor(
            cwd=tmp_path,
            role=AgentRole.CODER,
            permissions=PermissionConfig(mode="yolo"),
        )

    def test_shell_simple_command(self, executor: ToolExecutor) -> None:
        result = executor.execute(
            ToolCall(id="1", name="shell", arguments='{"command": "echo hello"}')
        )
        assert "hello" in result
        assert "[exit code: 0]" in result

    def test_shell_exit_code(self, executor: ToolExecutor) -> None:
        result = executor.execute(
            ToolCall(id="1", name="shell", arguments='{"command": "exit 42"}')
        )
        assert "[exit code: 42]" in result

    def test_shell_stderr(self, executor: ToolExecutor) -> None:
        result = executor.execute(
            ToolCall(id="1", name="shell", arguments='{"command": "echo err >&2"}')
        )
        assert "err" in result


# ---------------------------------------------------------------------------
# ToolExecutor — permission enforcement
# ---------------------------------------------------------------------------


class TestToolExecutorPermissions:
    def test_denied_tool_returns_error(self, tmp_path: Path) -> None:
        executor = ToolExecutor(
            cwd=tmp_path,
            role=AgentRole.CODER,
            permissions=PermissionConfig(mode="confined", allow_tools=("read",)),
        )
        result = executor.execute(
            ToolCall(
                id="1",
                name="write_file",
                arguments='{"path": "x.txt", "content": "bad"}',
            )
        )
        assert "not permitted" in result
        assert not (tmp_path / "x.txt").exists()

    def test_invalid_json_returns_error(self, tmp_path: Path) -> None:
        executor = ToolExecutor(
            cwd=tmp_path,
            role=AgentRole.CODER,
            permissions=PermissionConfig(mode="yolo"),
        )
        result = executor.execute(ToolCall(id="1", name="read_file", arguments="not json"))
        assert "invalid JSON" in result


# ---------------------------------------------------------------------------
# ToolExecutor — unknown tool
# ---------------------------------------------------------------------------


class TestToolExecutorUnknown:
    def test_unknown_tool_returns_error(self, tmp_path: Path) -> None:
        executor = ToolExecutor(
            cwd=tmp_path,
            role=AgentRole.CODER,
            permissions=PermissionConfig(mode="yolo"),
        )
        result = executor.execute(ToolCall(id="1", name="nonexistent_tool", arguments="{}"))
        assert "unknown tool" in result
