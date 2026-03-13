#!/usr/bin/env python3
"""approve_task.py — signal that the task passed QA review.

Usage:
  .orc/agent_tools/qa/approve_task.py <agent-id> <task-code> "<message>"

Arguments:
  agent-id    Your agent identifier, e.g. qa-2
  task-code   Zero-padded 4-digit task number, e.g. 0002
  message     One-line summary of the review outcome

Example:
  .orc/agent_tools/qa/approve_task.py qa-1 0002 "all tests green; no critical issues"

Commits all staged and unstaged tracked changes, then updates the board
task status to ``approved`` so the orchestrator triggers an automatic merge.
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
    orc_dir = _find_orc_dir()
    config_file = orc_dir / "config.yaml"
    cfg = yaml.safe_load(config_file.read_text()) if config_file.exists() else {}
    cfg = cfg or {}
    explicit = cfg.get("orc-cache-dir", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve() / "work"
    project_id = str(cfg.get("project-id", "")).strip()
    if project_id:
        xdg_env = os.environ.get("XDG_CACHE_HOME", "").strip()
        xdg = Path(xdg_env).expanduser().resolve() if xdg_env else Path.home() / ".cache"
        return xdg / "orc" / "projects" / project_id / "work"
    return orc_dir / "work"


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
        prog="approve_task.py",
        description="Signal that the task passed QA review.",
    )
    parser.add_argument("agent_id", help="Your agent identifier, e.g. qa-1")
    parser.add_argument("task_code", help="Zero-padded 4-digit task number, e.g. 0002")
    parser.add_argument("message", help="One-line summary of the review outcome")
    args = parser.parse_args()

    commit_msg = f"chore(qa/{args.task_code}): approved — {args.message}"
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": args.agent_id,
        "GIT_AUTHOR_EMAIL": f"{args.agent_id}@orc.local",
        "GIT_COMMITTER_NAME": args.agent_id,
        "GIT_COMMITTER_EMAIL": f"{args.agent_id}@orc.local",
    }
    result = subprocess.run(["git", "commit", "-a", "--allow-empty", "-m", commit_msg], env=env)
    if result.returncode != 0:
        sys.exit(result.returncode)

    work_dir = _resolve_work_dir()
    task_name = _find_task_by_code(args.task_code, work_dir)
    if task_name:
        _set_task_status(task_name, "approved", work_dir)
    else:
        print(  # noqa: T201
            f"Warning: task {args.task_code!r} not found on board; status not updated",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
