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

Stage any feedback files (e.g. the updated task .md) BEFORE calling this
script so they are included in the commit:
  git add .orc/work/0003-foo.md
  .orc/agent_tools/qa/reject_task.py qa-2 0003 "missing tests for error paths"

This commits ALL staged and unstaged tracked changes (git commit -a) and
produces a commit of the form:
  chore(qa-2.reject.0003): missing tests for error paths; see task file

The orchestrator reads this prefix to route the task back to a coder.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="reject_task.py",
        description="Signal that the task failed QA review.",
    )
    parser.add_argument("agent_id", help="Your agent identifier, e.g. qa-2")
    parser.add_argument("task_code", help="Zero-padded 4-digit task number, e.g. 0003")
    parser.add_argument("message", help="One-line summary of the blocking issue")
    args = parser.parse_args()

    commit_msg = f"chore({args.agent_id}.reject.{args.task_code}): {args.message}"
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
