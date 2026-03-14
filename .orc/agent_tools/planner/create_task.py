#!/usr/bin/env python3
"""create_task.py — create a new task and add it to the kanban board.

Usage:
  .orc/agent_tools/planner/create_task.py <task-title>

Arguments:
  task-title  Short dash-separated title, e.g. add-user-auth

Example:
  .orc/agent_tools/planner/create_task.py add-user-auth

This script calls the orc coordination API to:
1. Allocate the next task ID (counter in board.yaml)
2. Create ${TASK_ID}-${TASK_TITLE}.md from template
3. Add the task entry (status: planned) to the board
4. Print the absolute path of the created task file to stdout

The created task file should be edited to fill in the overview, scope,
steps, and notes before publishing with publish_task.py.

IMPORTANT: This tool MUST be run inside ``orc run``. Direct filesystem
access to ``.orc/`` is forbidden — use this script instead.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="create_task.py",
        description="Create a new task and add it to the kanban board.",
    )
    parser.add_argument("task_title", help="Short dash-separated title, e.g. add-user-auth")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from _orc_client import get_client  # noqa: PLC0415

    with get_client() as client:
        resp = client.post("/board/tasks", json={"title": args.task_title})
        resp.raise_for_status()
        print(resp.json()["path"])  # noqa: T201


if __name__ == "__main__":
    main()
