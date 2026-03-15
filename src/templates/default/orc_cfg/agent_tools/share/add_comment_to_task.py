#!/usr/bin/env python3
"""add_comment_to_task.py — append a comment to a task's conversation.

Usage:
  .orc/agent_tools/share/add_comment_to_task.py <agent-id> <task-code> "<comment>"

Arguments:
  agent-id   Your agent identifier, e.g. coder-1
  task-code  Zero-padded 4-digit task number, e.g. 0002
  comment    Comment text (quote if it contains spaces)

Examples:
  .orc/agent_tools/share/add_comment_to_task.py coder-1 0002 \
      "blocked: missing API spec for /auth endpoint"
  .orc/agent_tools/share/add_comment_to_task.py qa-1 0003 \
      "missing tests for the error paths in login handler"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="add_comment_to_task.py",
        description="Append a comment to a task's conversation.",
    )
    parser.add_argument("agent_id", help="Your agent identifier, e.g. coder-1")
    parser.add_argument("task_code", help="Zero-padded 4-digit task number, e.g. 0002")
    parser.add_argument("comment", help="Comment text")
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
        resp = client.post(
            f"/board/tasks/{task_name}/comments",
            json={"author": args.agent_id, "text": args.comment},
        )
        resp.raise_for_status()
        print(f"Comment added to {task_name}")  # noqa: T201


if __name__ == "__main__":
    main()
