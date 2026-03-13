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

This stages all changes (including new untracked files) and commits them,
producing a commit of the form:
  chore(coder-1.done.0002): implemented auth module; all tests green

The orchestrator reads this prefix to know the coder is done and routes
the task to QA.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="close_task.py",
        description="Signal that you have finished implementing a task.",
    )
    parser.add_argument("agent_id", help="Your agent identifier, e.g. coder-1")
    parser.add_argument("task_code", help="Zero-padded 4-digit task number, e.g. 0002")
    parser.add_argument("message", help="One-line summary of what was done")
    args = parser.parse_args()

    commit_msg = f"chore({args.agent_id}.done.{args.task_code}): {args.message}"

    result = subprocess.run(["git", "add", "-A"])
    if result.returncode != 0:
        sys.exit(result.returncode)

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": args.agent_id,
        "GIT_AUTHOR_EMAIL": f"{args.agent_id}@orc.local",
        "GIT_COMMITTER_NAME": args.agent_id,
        "GIT_COMMITTER_EMAIL": f"{args.agent_id}@orc.local",
    }
    result = subprocess.run(["git", "commit", "--allow-empty", "-m", commit_msg], env=env)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
