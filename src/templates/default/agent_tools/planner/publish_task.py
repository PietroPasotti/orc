#!/usr/bin/env python3
"""publish_task.py — commit a newly created task file (and updated board) to dev.

Usage:
  .orc/agent_tools/planner/publish_task.py <agent-id> <task-file> [extra-files...]

Arguments:
  agent-id    Your agent identifier, e.g. planner-1
  task-file   Path to the new task markdown, e.g. .orc/work/0003-add-foo.md
  extra-files Optional additional files to stage (e.g. .orc/work/board.yaml)

Example:
  .orc/agent_tools/planner/publish_task.py planner-1 .orc/work/0003-add-foo.md .orc/work/board.yaml

This stages the given files and produces a structured commit:
  chore(planner-1.ready.0003): add task 0003-add-foo

The commit format follows the same chore(<agent-id>.<action>.<task-code>)
convention used by coder and qa tools, allowing the git history to be
inspected uniformly across all agent roles.

All git commands must be run from inside the dev worktree.
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
        description="Commit a newly created task file (and updated board) to dev.",
    )
    parser.add_argument("agent_id", help="Your agent identifier, e.g. planner-1")
    parser.add_argument("task_file", help="Path to the new task markdown")
    parser.add_argument("extra_files", nargs="*", help="Additional files to stage")
    args = parser.parse_args()

    task_name = Path(args.task_file).stem  # e.g. 0003-add-foo
    task_code = task_name[:4]  # e.g. 0003

    files_to_stage = [args.task_file, *args.extra_files]

    result = subprocess.run(["git", "add", *files_to_stage])
    if result.returncode != 0:
        sys.exit(result.returncode)

    commit_msg = f"chore({args.agent_id}.ready.{task_code}): add task {task_name}"
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": args.agent_id,
        "GIT_AUTHOR_EMAIL": f"{args.agent_id}@orc.local",
        "GIT_COMMITTER_NAME": args.agent_id,
        "GIT_COMMITTER_EMAIL": f"{args.agent_id}@orc.local",
    }
    result = subprocess.run(["git", "commit", "-m", commit_msg], env=env)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
