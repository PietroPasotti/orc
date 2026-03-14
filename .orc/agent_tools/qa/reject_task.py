#!/usr/bin/env python3
"""reject_task.py — signal that the task failed QA review.

Usage:
  .orc/agent_tools/qa/reject_task.py <agent-id> <task-code> "<message>"

Arguments:
  agent-id    Your agent identifier, e.g. qa-2
  task-code   Zero-padded 4-digit task number, e.g. 0003
  message     One-line summary of the blocking issue

Example:
  .orc/agent_tools/qa/reject_task.py qa-2 0003 "missing tests for error paths; see task file"

Commits all staged and unstaged tracked changes, then updates the board
task status to ``rejected`` and posts the rejection message as a comment so
the coder knows what to fix.

IMPORTANT: This tool MUST be run inside ``orc run``. Direct filesystem
access to ``.orc/`` is forbidden — use this script instead.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="reject_task.py",
        description="Signal that the task failed QA review.",
    )
    parser.add_argument("agent_id", help="Your agent identifier, e.g. qa-2")
    parser.add_argument("task_code", help="Zero-padded 4-digit task number, e.g. 0003")
    parser.add_argument("message", help="One-line summary of the blocking issue")
    args = parser.parse_args()

    commit_msg = f"chore(qa/{args.task_code}): rejected — {args.message}"
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

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from _orc_client import find_task_by_code, get_client  # noqa: PLC0415

    with get_client() as client:
        task_name = find_task_by_code(client, args.task_code)
        if task_name is None:
            print(  # noqa: T201
                f"Error: task {args.task_code!r} not found on board",
                file=sys.stderr,
            )
            sys.exit(1)
        resp = client.put(f"/board/tasks/{task_name}/status", json={"status": "rejected"})
        resp.raise_for_status()
        resp = client.post(
            f"/board/tasks/{task_name}/comments",
            json={"author": args.agent_id, "text": args.message},
        )
        resp.raise_for_status()


if __name__ == "__main__":
    main()
