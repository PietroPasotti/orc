#!/usr/bin/env python3
"""publish_task.py — commit a newly created task file to dev.

Usage:
  .orc/agent_tools/planner/publish_task.py <agent-id> <task-file>

Arguments:
  agent-id    Your agent identifier, e.g. planner-1
  task-file   Path to the new task markdown, e.g. .orc/work/0003-add-foo.md

Example:
  .orc/agent_tools/planner/publish_task.py planner-1 .orc/work/0003-add-foo.md

Stages the task file and commits it to the dev branch.  The board.yaml is
NOT staged — it lives in the project cache, not in git.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="publish_task.py",
        description="Commit a newly created task file to dev.",
    )
    parser.add_argument("agent_id", help="Your agent identifier, e.g. planner-1")
    parser.add_argument("task_file", help="Path to the new task markdown")
    args = parser.parse_args()

    task_name = Path(args.task_file).stem

    result = subprocess.run(["git", "add", args.task_file])
    if result.returncode != 0:
        sys.exit(result.returncode)

    commit_msg = f"chore({args.agent_id}): add task {task_name}"
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
