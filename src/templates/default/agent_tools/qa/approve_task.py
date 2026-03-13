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

This commits ALL staged and unstaged tracked changes (git commit -a) and
produces a commit of the form:
  chore(qa-1.approve.0002): all tests green; no critical issues

The orchestrator reads this prefix to trigger an automatic merge into dev.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="approve_task.py",
        description="Signal that the task passed QA review.",
    )
    parser.add_argument("agent_id", help="Your agent identifier, e.g. qa-1")
    parser.add_argument("task_code", help="Zero-padded 4-digit task number, e.g. 0002")
    parser.add_argument("message", help="One-line summary of the review outcome")
    args = parser.parse_args()

    commit_msg = f"chore({args.agent_id}.approve.{args.task_code}): {args.message}"
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": args.agent_id,
        "GIT_AUTHOR_EMAIL": f"{args.agent_id}@orc.local",
        "GIT_COMMITTER_NAME": args.agent_id,
        "GIT_COMMITTER_EMAIL": f"{args.agent_id}@orc.local",
    }
    result = subprocess.run(["git", "commit", "-a", "--allow-empty", "-m", commit_msg], env=env)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
