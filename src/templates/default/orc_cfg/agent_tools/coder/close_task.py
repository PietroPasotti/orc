#!/usr/bin/env python3
"""close_task.py — signal that you have finished implementing a task.

Usage:
  .orc/agent_tools/coder/close_task.py <agent-id> <task-code> "<message>"

Arguments:
  agent-id    Your agent identifier, e.g. coder-1
  task-code   Zero-padded 4-digit task number, e.g. 0002
  message     One-line summary of what was done

Example:
  .orc/agent_tools/coder/close_task.py coder-1 0002 "implemented auth module; all tests green"

Stages all changes and commits them, then updates the board task status to
``review`` so the orchestrator routes the task to QA.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import yaml
from filelock import FileLock

_LOCK_TIMEOUT = 30


def _find_orc_dir() -> Path:
    for parent in [Path.cwd(), *Path.cwd().parents]:
        candidate = parent / ".orc"
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("Could not find .orc directory")


def _resolve_work_dir() -> Path:
    return _find_orc_dir() / "work"


def _find_task_by_code(task_code: str, work_dir: Path) -> str | None:
    board_file = work_dir / "board.yaml"
    if not board_file.exists():
        return None
    board = yaml.safe_load(board_file.read_text()) or {}
    for entry in board.get("open", []):
        t = entry if isinstance(entry, dict) else {"name": str(entry)}
        if t.get("name", "").startswith(task_code):
            return t["name"]
    return None


def _set_task_status(task_name: str, status: str, work_dir: Path) -> None:
    board_file = work_dir / "board.yaml"
    if not board_file.exists():
        return
    with FileLock(str(work_dir / ".board.lock"), timeout=_LOCK_TIMEOUT):
        board = yaml.safe_load(board_file.read_text()) or {}
        for i, entry in enumerate(board.get("open", [])):
            t = entry if isinstance(entry, dict) else {"name": str(entry)}
            if t.get("name") == task_name:
                t["status"] = status
                board["open"][i] = t
                tmp = board_file.with_suffix(".yaml.tmp")
                tmp.write_text(yaml.dump(board, default_flow_style=False, allow_unicode=True))
                tmp.replace(board_file)
                return


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="close_task.py",
        description="Signal that you have finished implementing a task.",
    )
    parser.add_argument("agent_id", help="Your agent identifier, e.g. coder-1")
    parser.add_argument("task_code", help="Zero-padded 4-digit task number, e.g. 0002")
    parser.add_argument("message", help="One-line summary of what was done")
    args = parser.parse_args()

    commit_msg = f"feat({args.task_code}): {args.message}"
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": args.agent_id,
        "GIT_AUTHOR_EMAIL": f"{args.agent_id}@orc.local",
        "GIT_COMMITTER_NAME": args.agent_id,
        "GIT_COMMITTER_EMAIL": f"{args.agent_id}@orc.local",
    }

    result = subprocess.run(["git", "add", "-A"])
    if result.returncode != 0:
        sys.exit(result.returncode)

    result = subprocess.run(["git", "commit", "--allow-empty", "-m", commit_msg], env=env)
    if result.returncode != 0:
        sys.exit(result.returncode)

    work_dir = _resolve_work_dir()
    task_name = _find_task_by_code(args.task_code, work_dir)
    if task_name:
        _set_task_status(task_name, "review", work_dir)
    else:
        print(  # noqa: T201
            f"Warning: task {args.task_code!r} not found on board; status not updated",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
