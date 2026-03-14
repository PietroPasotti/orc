#!/usr/bin/env python3
"""get_task.py — print the contents of a task file.

Usage:
  .orc/agent_tools/coder/get_task.py <task-filename>

Arguments:
  task-filename  The task filename, e.g. 0003-add-user-auth.md

Example:
  .orc/agent_tools/coder/get_task.py 0003-add-user-auth.md

Prints the full text of the task file to stdout.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="get_task.py",
        description="Fetch task file content from the coordination server.",
    )
    parser.add_argument("task_filename", help="Task filename, e.g. 0003-add-user-auth.md")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from _orc_client import get_client  # noqa: PLC0415

    with get_client() as client:
        resp = client.get(f"/board/tasks/{args.task_filename}/content")
        if resp.status_code == 404:
            print(f"Error: task {args.task_filename!r} not found", file=sys.stderr)  # noqa: T201
            sys.exit(1)
        resp.raise_for_status()
        print(resp.json()["content"])  # noqa: T201


if __name__ == "__main__":
    main()
