#!/usr/bin/env python3
"""create_task.py — create a new task and add it to the kanban board.

Usage:
  .orc/agent_tools/planner/create_task.py <task-title>

Arguments:
  task-title  Short dash-separated title, e.g. add-user-auth

Example:
  .orc/agent_tools/planner/create_task.py add-user-auth

This script:
1. Resolves the board location from .orc/config.yaml (reads project-id and
   orc-cache-dir; defaults to ~/.cache/orc/projects/<project-id>/)
2. Reads the current counter from board.yaml
3. Formats the task ID as a 4-digit zero-padded string (e.g. 0005)
4. Creates ${TASK_ID}-${TASK_TITLE}.md from template
5. Adds the task entry (with status: planned) to the 'open' list in board.yaml
6. Increments the counter and writes it back
7. Prints the created filename to stdout

The created task file should be edited to fill in the overview, scope,
steps, and notes before publishing with publish_task.py.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

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


def _find_orc_dir() -> Path:
    for parent in [Path.cwd(), *Path.cwd().parents]:
        candidate = parent / ".orc"
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("Could not find .orc directory")


def _resolve_work_dir() -> Path:
    orc_dir = _find_orc_dir()
    config_file = orc_dir / "config.yaml"
    cfg = yaml.safe_load(config_file.read_text()) if config_file.exists() else {}
    cfg = cfg or {}

    explicit = cfg.get("orc-cache-dir", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve() / "work"

    project_id = str(cfg.get("project-id", "")).strip()
    if project_id:
        xdg_env = os.environ.get("XDG_CACHE_HOME", "").strip()
        xdg = Path(xdg_env).expanduser().resolve() if xdg_env else Path.home() / ".cache"
        return xdg / "orc" / "projects" / project_id / "work"

    return orc_dir / "work"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="create_task.py",
        description="Create a new task and add it to the kanban board.",
    )
    parser.add_argument("task_title", help="Short dash-separated title, e.g. add-user-auth")
    args = parser.parse_args()

    task_title: str = args.task_title
    work_dir = _resolve_work_dir()
    work_dir.mkdir(parents=True, exist_ok=True)
    board_file = work_dir / "board.yaml"

    if not board_file.exists():
        print(f"Error: board file not found at {board_file}", file=sys.stderr)  # noqa: T201
        sys.exit(1)

    board = yaml.safe_load(board_file.read_text()) or {}
    board.setdefault("counter", 0)
    board.setdefault("open", [])
    board.setdefault("done", [])

    counter: int = board["counter"]
    task_id = f"{counter:04d}"
    task_filename = f"{task_id}-{task_title}.md"
    task_file = work_dir / task_filename

    task_file.write_text(TASK_TEMPLATE.format(task_id=task_id, task_title=task_title))

    board["open"].append({"name": task_filename, "status": "planned"})
    board["counter"] = counter + 1

    tmp = board_file.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.dump(board, default_flow_style=False, allow_unicode=True))
    tmp.replace(board_file)

    print(str(task_file))  # noqa: T201


if __name__ == "__main__":
    main()
