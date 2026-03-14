#!/usr/bin/env python3
"""create_task.py — create a new task, write it to the board, and commit it to dev.

Usage:
  echo '<json>' | .orc/agent_tools/planner/create_task.py \\
    <agent-id> <task-title> <vision-file> [extra-file...]

Arguments:
  agent-id      Your agent identifier, e.g. planner-1
  task-title    Short dash-separated title, e.g. add-user-auth
  vision-file   Filename of the vision this task was refined from, e.g. 0001-my-vision.md
  extra-file    Optional extra files to stage (e.g. ADR docs you created)

Body (JSON on stdin):
  {
    "overview":      "<free-form description of what and why>",
    "in_scope":      ["item 1", "item 2"],
    "out_of_scope":  ["item 1"],
    "steps":         ["step 1", "step 2"],
    "notes":         "<optional free-form notes>"
  }

Example:
  echo '{
    "overview": "Add JWT-based authentication to the API.",
    "in_scope": ["login endpoint", "token refresh"],
    "out_of_scope": ["OAuth integration", "UI changes"],
    "steps": ["Write failing tests", "Implement auth middleware", "Wire into routes"],
    "notes": "See ADR-0042 for the chosen algorithm."
  }' | .orc/agent_tools/planner/create_task.py planner-1 add-user-auth 0001-auth-vision.md

  # With an extra ADR file:
  echo '{...}' | .orc/agent_tools/planner/create_task.py \\
    planner-1 add-user-auth 0001-auth-vision.md docs/adr/0042-auth.md

This script:
1. Reads the structured task body from stdin (JSON).
2. Calls POST /board/tasks — the server assembles the markdown and adds it to the board.
3. Commits the result to dev with git (signals the orchestrator that planning is done).
4. Prints the filename of the created task file to stdout.

IMPORTANT: This tool MUST be run inside ``orc run``. Direct filesystem
access to ``.orc/`` is forbidden — use this script instead.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="create_task.py",
        description="Create a new task, write it to the board, and commit it to dev.",
    )
    parser.add_argument("agent_id", help="Your agent identifier, e.g. planner-1")
    parser.add_argument("task_title", help="Short dash-separated title, e.g. add-user-auth")
    parser.add_argument(
        "vision_file",
        help="Filename of the vision this task was refined from, e.g. 0001-my-vision.md",
    )
    parser.add_argument("extra_files", nargs="*", help="Extra files to stage (e.g. ADR docs)")
    args = parser.parse_args()

    try:
        body = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON on stdin: {exc}", file=sys.stderr)  # noqa: T201
        sys.exit(1)

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from _orc_client import get_client  # noqa: PLC0415

    with get_client() as client:
        resp = client.post(
            "/board/tasks",
            json={"title": args.task_title, "vision": args.vision_file, "body": body},
        )
        resp.raise_for_status()
        filename = resp.json()["filename"]

    if args.extra_files:
        result = subprocess.run(["git", "add", "--"] + args.extra_files)
        if result.returncode != 0:
            sys.exit(result.returncode)

    task_name = Path(filename).stem
    commit_msg = f"chore({args.agent_id}): add task {task_name}"
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": args.agent_id,
        "GIT_AUTHOR_EMAIL": f"{args.agent_id}@orc.local",
        "GIT_COMMITTER_NAME": args.agent_id,
        "GIT_COMMITTER_EMAIL": f"{args.agent_id}@orc.local",
    }
    result = subprocess.run(["git", "commit", "--allow-empty", "-m", commit_msg], env=env)
    if result.returncode != 0:
        sys.exit(result.returncode)

    print(filename)  # noqa: T201


if __name__ == "__main__":
    main()
