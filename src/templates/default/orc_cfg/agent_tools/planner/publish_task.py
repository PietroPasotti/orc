#!/usr/bin/env python3
"""publish_task.py — commit a newly created task to dev.

Usage:
  .orc/agent_tools/planner/publish_task.py <agent-id> <task-name> [extra-file...]

Arguments:
  agent-id    Your agent identifier, e.g. planner-1
  task-name   Task filename or name, e.g. 0003-add-foo or 0003-add-foo.md
  extra-file  Optional extra files to stage (e.g. ADR docs you created)

Example:
  .orc/agent_tools/planner/publish_task.py planner-1 0003-add-foo
  .orc/agent_tools/planner/publish_task.py planner-1 0003-add-foo docs/adr/0042-foo.md

The task file and board.yaml live in the project cache — they are NOT staged for
git.  Any extra-files you pass (e.g. ADRs) ARE staged before the commit.

The board is already updated by create_task.py (status: planned).  This commit
records the planner's work in the git history and acts as the hand-off signal.
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
        description="Commit a newly created task to dev.",
    )
    parser.add_argument("agent_id", help="Your agent identifier, e.g. planner-1")
    parser.add_argument("task_name", help="Task name, e.g. 0003-add-foo or 0003-add-foo.md")
    parser.add_argument("extra_files", nargs="*", help="Extra files to stage (e.g. ADR docs)")
    args = parser.parse_args()

    task_name = Path(args.task_name).stem

    if args.extra_files:
        result = subprocess.run(["git", "add", "--"] + args.extra_files)
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
