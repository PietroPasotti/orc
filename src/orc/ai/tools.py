"""Built-in tool system for the internal agentic loop.

Provides file, shell, and ORC board tools that replace the CLI's built-in
capabilities.  Tools are executed in-process (no MCP server needed).

Permission enforcement
----------------------
:class:`PermissionChecker` validates each tool call against the agent's
:class:`~orc.squad.PermissionConfig` before execution.  In ``yolo`` mode
everything is allowed; in ``confined`` mode each tool maps to a permission
category that must appear in the allow list and must not appear in the deny list.

Tool definitions
----------------
:func:`get_tool_definitions` returns OpenAI-format tool schemas filtered by
the agent's role.
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import structlog

from orc.ai.llm import ToolCall
from orc.squad import AgentRole, PermissionConfig

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Permission checking
# ---------------------------------------------------------------------------

# Maps tool names to the permission category they require.
_TOOL_PERMISSION_MAP: dict[str, str] = {
    "read_file": "read",
    "list_directory": "read",
    "write_file": "write",
    "edit_file": "write",
    "shell": "shell",
    # ORC board tools
    "get_task": "orc",
    "update_task_status": "orc",
    "add_comment": "orc",
    "get_vision": "orc",
    "create_task": "orc",
    "close_vision": "orc",
    "close_task": "orc",
    "close_merge": "orc",
    "review_task": "orc",
}


class PermissionChecker:
    """Validates tool calls against a :class:`PermissionConfig`."""

    def __init__(self, config: PermissionConfig) -> None:
        self.config = config

    def is_allowed(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Return ``True`` if the tool call is permitted."""
        if self.config.is_yolo:
            return True

        category = _TOOL_PERMISSION_MAP.get(tool_name)
        if category is None:
            return False

        if category == "shell":
            command = arguments.get("command", "")
            return self._check_shell(command)

        # Check deny list first.
        if category in self.config.deny_tools:
            return False
        # Check allow list.
        return category in self.config.allow_tools

    def _check_shell(self, command: str) -> bool:
        """Check a shell command against shell permission patterns.

        Shell permissions use patterns like ``shell(git:*)``, meaning
        ``git`` commands with any arguments are allowed.  The command
        must match at least one allow pattern and not match any deny pattern.

        Compound commands (``&&``, ``||``, ``;``) are split and each
        segment is checked independently — all must be allowed.
        """
        segments = self._split_compound_command(command)
        return all(self._check_single_command(seg) for seg in segments)

    def _check_single_command(self, command: str) -> bool:
        """Check a single (non-compound) command against permission patterns."""
        for deny in self.config.deny_tools:
            if deny.startswith("shell(") and deny.endswith(")"):
                pattern = deny[6:-1]
                if self._matches_shell_pattern(command, pattern):
                    return False

        for allow in self.config.allow_tools:
            if allow.startswith("shell(") and allow.endswith(")"):
                pattern = allow[6:-1]
                if self._matches_shell_pattern(command, pattern):
                    return True

        return False

    @staticmethod
    def _split_compound_command(command: str) -> list[str]:
        """Split a compound shell command on ``&&``, ``||``, ``;``.

        Returns a list of individual command segments, stripped.
        Ignores operators inside quotes (simple heuristic).
        """
        import re  # noqa: PLC0415

        # Split on &&, ||, ; but not inside quotes.
        segments = re.split(r"\s*(?:&&|\|\||;)\s*", command.strip())
        # Also strip leading cd commands that are just changing directory.
        result = []
        for seg in segments:
            seg = seg.strip()
            if seg and not re.match(r"^cd\s+", seg):
                result.append(seg)
        return result or [command.strip()]

    @staticmethod
    def _matches_shell_pattern(command: str, pattern: str) -> bool:
        """Check whether *command* matches a shell permission pattern.

        Pattern format: ``<prefix>:<glob>`` where the prefix is matched
        against the start of the command and the glob matches the rest.
        For example, ``git:*`` matches any command starting with ``git``
        and ``git push:*`` matches any ``git push ...`` command.
        """
        parts = pattern.split(":", 1)
        if len(parts) != 2:
            return False
        prefix, glob = parts
        prefix = prefix.strip()
        glob = glob.strip()

        cmd = command.strip()
        if not cmd.startswith(prefix):
            return False

        rest = cmd[len(prefix) :].lstrip()
        return fnmatch.fnmatch(rest, glob)


# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling format)
# ---------------------------------------------------------------------------

_FILE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the contents of a file. Returns the file content with line numbers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file (relative to working directory).",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file with the given content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file (relative to working directory).",
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write to the file.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Make a surgical edit to a file by replacing an exact string match. "
                "The old_str must match exactly one occurrence in the file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file (relative to working directory).",
                    },
                    "old_str": {
                        "type": "string",
                        "description": "The exact string to find and replace.",
                    },
                    "new_str": {
                        "type": "string",
                        "description": "The replacement string.",
                    },
                },
                "required": ["path", "old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and directories at the given path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Path to the directory "
                            "(relative to working directory). "
                            "Defaults to '.'."
                        ),
                    },
                },
            },
        },
    },
]

_SHELL_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "shell",
        "description": (
            "Execute a shell command. Returns stdout and stderr. "
            "Commands run in the agent's working directory."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute.",
                },
            },
            "required": ["command"],
        },
    },
}

# ORC board tools — definitions generated from MCP tool functions.
_ORC_SHARED_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_task",
            "description": "Fetch a task's markdown content and conversation thread.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_filename": {
                        "type": "string",
                        "description": 'Full task filename, e.g. "0003-add-user-auth.md".',
                    },
                },
                "required": ["task_filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_task_status",
            "description": "Change a task's status on the board.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_code": {
                        "type": "string",
                        "description": 'Four-digit zero-padded task number, e.g. "0002".',
                    },
                    "status": {
                        "type": "string",
                        "description": (
                            "New status: planned, in-progress, in-review, done, blocked, stuck."
                        ),
                    },
                },
                "required": ["task_code", "status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_comment",
            "description": "Append a comment to a task's conversation thread.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_code": {
                        "type": "string",
                        "description": 'Four-digit zero-padded task number, e.g. "0002".',
                    },
                    "comment": {
                        "type": "string",
                        "description": "Comment text to append.",
                    },
                },
                "required": ["task_code", "comment"],
            },
        },
    },
]

_ORC_PLANNER_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_vision",
            "description": "Fetch the content of a vision document.",
            "parameters": {
                "type": "object",
                "properties": {
                    "vision_filename": {
                        "type": "string",
                        "description": 'Vision filename, e.g. "0001-shark-fleet.md".',
                    },
                },
                "required": ["vision_filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Create a new task on the board.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_title": {
                        "type": "string",
                        "description": 'Dash-separated title, e.g. "add-user-auth".',
                    },
                    "vision_file": {
                        "type": "string",
                        "description": 'Source vision filename, e.g. "0001-auth-vision.md".',
                    },
                    "overview": {"type": "string", "description": "Short description."},
                    "in_scope": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Items in scope.",
                    },
                    "out_of_scope": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Items out of scope.",
                    },
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Implementation steps.",
                    },
                    "notes": {"type": "string", "description": "Optional notes."},
                    "extra_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional extra files to stage.",
                    },
                },
                "required": [
                    "task_title",
                    "vision_file",
                    "overview",
                    "in_scope",
                    "out_of_scope",
                    "steps",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_vision",
            "description": "Mark a vision as complete.",
            "parameters": {
                "type": "object",
                "properties": {
                    "vision_file": {
                        "type": "string",
                        "description": 'Vision filename, e.g. "0001-shark-fleet.md".',
                    },
                    "summary": {
                        "type": "string",
                        "description": "2-4 sentence summary of what was accomplished.",
                    },
                    "task_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Task filenames that implement this vision.",
                    },
                },
                "required": ["vision_file", "summary"],
            },
        },
    },
]

_ORC_CODER_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "close_task",
            "description": (
                "Signal implementation complete. Stages, commits, sets status to in-review."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_code": {
                        "type": "string",
                        "description": 'Four-digit zero-padded task number, e.g. "0002".',
                    },
                    "message": {
                        "type": "string",
                        "description": "Short description of what was implemented.",
                    },
                },
                "required": ["task_code", "message"],
            },
        },
    },
]

_ORC_QA_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "review_task",
            "description": (
                "Signal QA review outcome. 'done' to approve, 'in-progress' to reject."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_code": {
                        "type": "string",
                        "description": 'Four-digit zero-padded task number, e.g. "0002".',
                    },
                    "outcome": {
                        "type": "string",
                        "description": "'done' to approve, 'in-progress' to reject.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Summary of the review outcome.",
                    },
                },
                "required": ["task_code", "outcome", "message"],
            },
        },
    },
]

_ORC_MERGER_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "close_merge",
            "description": (
                "Signal that a feature branch has been merged. "
                "Stages, commits, and deletes the task from board."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_code": {
                        "type": "string",
                        "description": 'Four-digit zero-padded task number, e.g. "0002".',
                    },
                    "message": {
                        "type": "string",
                        "description": "Short description of the merge result.",
                    },
                },
                "required": ["task_code", "message"],
            },
        },
    },
]

_ROLE_TOOLS: dict[str, list[dict[str, Any]]] = {
    "planner": _ORC_PLANNER_TOOLS,
    "coder": _ORC_CODER_TOOLS,
    "qa": _ORC_QA_TOOLS,
    "merger": _ORC_MERGER_TOOLS,
}


def get_tool_definitions(role: AgentRole | str) -> list[dict[str, Any]]:
    """Return OpenAI-format tool definitions filtered by *role*.

    Every role gets file tools, shell, and shared ORC tools.
    Role-specific ORC tools are added on top.
    """
    tools = list(_FILE_TOOLS) + [_SHELL_TOOL] + list(_ORC_SHARED_TOOLS)
    role_str = role.value if isinstance(role, AgentRole) else role
    extras = _ROLE_TOOLS.get(role_str, [])
    tools.extend(extras)
    return tools


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

# Maximum output size from shell commands (chars).
_MAX_SHELL_OUTPUT = 10_000
_SHELL_TIMEOUT = 120  # seconds

# Maximum file size for read_file (bytes).
_MAX_READ_SIZE = 512 * 1024  # 512 KB


class ToolExecutor:
    """Executes tool calls for an agent.

    Parameters
    ----------
    cwd:
        Working directory for file and shell operations (agent's worktree).
    role:
        Agent role (determines which ORC tools are available).
    permissions:
        Permission config for enforcement.
    socket_path:
        ORC API socket path (for board tools).
    agent_id:
        Agent identifier (for board tools).
    """

    def __init__(
        self,
        cwd: Path,
        role: AgentRole | str,
        permissions: PermissionConfig,
        socket_path: str = "",
        agent_id: str = "",
    ) -> None:
        self.cwd = cwd
        self.role = role
        self.permissions = permissions
        self.socket_path = socket_path
        self.agent_id = agent_id
        self._checker = PermissionChecker(permissions)
        self._definitions = get_tool_definitions(role)

    @property
    def definitions(self) -> list[dict[str, Any]]:
        """Tool definitions in OpenAI function-calling format."""
        return self._definitions

    def execute(self, tool_call: ToolCall) -> str:
        """Execute a tool call and return the result as a string.

        On permission denial or execution error, returns an error message
        string (never raises).
        """
        try:
            args = json.loads(tool_call.arguments)
        except json.JSONDecodeError as exc:
            return f"Error: invalid JSON arguments: {exc}"

        if not self._checker.is_allowed(tool_call.name, args):
            return (
                f"Error: tool {tool_call.name!r} is not permitted by the current permission config."
            )

        try:
            return self._dispatch(tool_call.name, args)
        except Exception as exc:
            logger.warning("tool_error", tool=tool_call.name, error=str(exc))
            return f"Error executing {tool_call.name}: {exc}"

    def _dispatch(self, name: str, args: dict[str, Any]) -> str:
        """Route a tool call to the appropriate handler."""
        match name:
            # File tools
            case "read_file":
                return self._read_file(args["path"])
            case "write_file":
                return self._write_file(args["path"], args["content"])
            case "edit_file":
                return self._edit_file(args["path"], args["old_str"], args["new_str"])
            case "list_directory":
                return self._list_directory(args.get("path", "."))
            case "shell":
                return self._shell(args["command"])
            # ORC board tools — delegate to mcp.tools functions
            case (
                "get_task"
                | "update_task_status"
                | "add_comment"
                | "get_vision"
                | "create_task"
                | "close_vision"
                | "close_task"
                | "close_merge"
                | "review_task"
            ):
                return self._orc_tool(name, args)
            case _:
                return f"Error: unknown tool {name!r}"

    # ------------------------------------------------------------------
    # File tools
    # ------------------------------------------------------------------

    def _read_file(self, path: str) -> str:
        target = (self.cwd / path).resolve()
        if not target.is_file():
            return f"Error: {path!r} is not a file or does not exist."
        size = target.stat().st_size
        if size > _MAX_READ_SIZE:
            return (
                f"Error: file {path!r} is {size:,} bytes (max {_MAX_READ_SIZE:,}). "
                "Use shell('head -n ...') or shell('tail -n ...') to read portions."
            )
        content = target.read_text(errors="replace")
        lines = content.splitlines()
        numbered = "\n".join(f"{i + 1:4d} | {line}" for i, line in enumerate(lines))
        return numbered

    def _write_file(self, path: str, content: str) -> str:
        target = (self.cwd / path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return f"Wrote {len(content)} chars to {path}."

    def _edit_file(self, path: str, old_str: str, new_str: str) -> str:
        target = (self.cwd / path).resolve()
        if not target.is_file():
            return f"Error: {path!r} does not exist."
        content = target.read_text()
        count = content.count(old_str)
        if count == 0:
            return f"Error: old_str not found in {path!r}."
        if count > 1:
            return f"Error: old_str found {count} times in {path!r} (must be unique)."
        new_content = content.replace(old_str, new_str, 1)
        target.write_text(new_content)
        return f"Edited {path}: replaced 1 occurrence."

    def _list_directory(self, path: str) -> str:
        target = (self.cwd / path).resolve()
        if not target.is_dir():
            return f"Error: {path!r} is not a directory or does not exist."
        entries = sorted(target.iterdir())
        lines: list[str] = []
        for entry in entries:
            if entry.name.startswith("."):
                continue
            suffix = "/" if entry.is_dir() else ""
            lines.append(f"{entry.name}{suffix}")
        return "\n".join(lines) if lines else "(empty directory)"

    # ------------------------------------------------------------------
    # Shell tool
    # ------------------------------------------------------------------

    def _shell(self, command: str) -> str:
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=_SHELL_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {_SHELL_TIMEOUT}s."

        output = result.stdout + result.stderr
        if len(output) > _MAX_SHELL_OUTPUT:
            output = (
                f"... (truncated, showing last {_MAX_SHELL_OUTPUT} chars)\n"
                + output[-_MAX_SHELL_OUTPUT:]
            )

        exit_info = f"\n[exit code: {result.returncode}]"
        return output + exit_info

    # ------------------------------------------------------------------
    # ORC board tools (delegate to mcp.tools)
    # ------------------------------------------------------------------

    def _orc_tool(self, name: str, args: dict[str, Any]) -> str:
        """Call an ORC board tool function from :mod:`orc.mcp.tools`.

        Sets the required environment variables so the tool functions can
        communicate with the ORC coordination API.  Also temporarily changes
        the process CWD to the agent's worktree so that git operations
        (e.g. ``close_task``) run in the right repository.
        """
        import orc.mcp.tools as mcp_tools  # noqa: PLC0415

        # Set env vars that MCP tool functions expect.
        old_env: dict[str, str | None] = {}
        env_vars = {
            "ORC_API_SOCKET": self.socket_path,
            "ORC_AGENT_ID": self.agent_id,
            "ORC_AGENT_ROLE": self.role.value if isinstance(self.role, AgentRole) else self.role,
        }
        for k, v in env_vars.items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v

        # Change to the agent's worktree so git commands run there.
        old_cwd = Path.cwd()
        os.chdir(self.cwd)

        try:
            func = getattr(mcp_tools, name, None)
            if func is None:
                return f"Error: unknown ORC tool {name!r}"
            return str(func(**args))
        finally:
            # Restore CWD and env.
            os.chdir(old_cwd)
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
