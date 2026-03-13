#!/usr/bin/env python3
"""create_task.py — create a new task and add it to the kanban board.

Usage:
  .orc/agent_tools/planner/create_task.py <task-title>

Arguments:
  task-title  Short dash-separated title, e.g. add-user-auth

Example:
  .orc/agent_tools/planner/create_task.py add-user-auth

This script:
1. Reads the current counter from .orc/work/board.yaml
2. Formats the task ID as a 4-digit zero-padded string (e.g. 0005)
3. Creates .orc/work/${TASK_ID}-${TASK_TITLE}.md from template
4. Adds the task filename to the 'open' list in board.yaml
5. Increments the counter and writes it back
6. Prints the created filename to stdout

The created task file should be edited to fill in the overview, scope,
steps, and notes before publishing with publish_task.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

BOARD_FILE = Path(".orc/work/board.yaml")

TASK_TEMPLATE = """\
# {task_id}-{task_title}

## Overview

<!-- What and why -->

## Scope

**In scope:**
- 

**Out of scope:**
- 

## Steps

- [ ] 

## Notes

"""


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="create_task.py",
        description="Create a new task and add it to the kanban board.",
    )
    parser.add_argument("task_title", help="Short dash-separated title, e.g. add-user-auth")
    args = parser.parse_args()

    task_title: str = args.task_title

    if not BOARD_FILE.exists():
        print(f"Error: board file not found at {BOARD_FILE}", file=sys.stderr)  # noqa: T201
        sys.exit(1)

    board = yaml.safe_load(BOARD_FILE.read_text()) or {}
    board.setdefault("counter", 0)
    board.setdefault("open", [])
    board.setdefault("done", [])

    counter: int = board["counter"]
    task_id = f"{counter:04d}"
    task_filename = f"{task_id}-{task_title}.md"
    task_file = Path(".orc/work") / task_filename

    # Create the task file from template
    task_file.write_text(TASK_TEMPLATE.format(task_id=task_id, task_title=task_title))

    # Update board: add to open list and increment counter
    board["open"].append({"name": task_filename})
    board["counter"] = counter + 1

    tmp = BOARD_FILE.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.dump(board, default_flow_style=False, allow_unicode=True))
    tmp.replace(BOARD_FILE)

    print(str(task_file))  # noqa: T201


if __name__ == "__main__":
    main()
