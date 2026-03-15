#!/usr/bin/env python3
"""update_task.py — change the status of a task on the board.

Usage:
  .orc/agent_tools/share/update_task.py <task-code> <status>

Arguments:
  task-code  Zero-padded 4-digit task number, e.g. 0002
  status     New status: planned | in-progress | in-review | done | blocked

Examples:
  .orc/agent_tools/share/update_task.py 0002 blocked
  .orc/agent_tools/share/update_task.py 0003 in-progress
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_VALID_STATUSES = {"planned", "in-progress", "in-review", "done", "blocked"}


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="update_task.py",
        description="Change the status of a task on the board.",
    )
    parser.add_argument("task_code", help="Zero-padded 4-digit task number, e.g. 0002")
    parser.add_argument(
        "status",
        choices=sorted(_VALID_STATUSES),
        help="New task status",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from _orc_client import find_task_by_code, get_client  # noqa: PLC0415

    with get_client() as client:
        task_name = find_task_by_code(client, args.task_code)
        if task_name is None:
            print(  # noqa: T201
                f"Error: task {args.task_code!r} not found on board",
                file=sys.stderr,
            )
            sys.exit(1)
        resp = client.put(f"/board/tasks/{task_name}/status", json={"status": args.status})
        resp.raise_for_status()
        print(f"Task {task_name} status set to '{args.status}'")  # noqa: T201


if __name__ == "__main__":
    main()
