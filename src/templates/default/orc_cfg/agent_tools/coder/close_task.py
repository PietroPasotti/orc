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
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


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
        resp = client.put(f"/board/tasks/{task_name}/status", json={"status": "in-review"})
        resp.raise_for_status()


if __name__ == "__main__":
    main()
