#!/usr/bin/env python3
"""get_task.py — print the full details of a task, including its conversation.

Usage:
  .orc/agent_tools/share/get_task.py <task-filename>

Arguments:
  task-filename  The task filename, e.g. 0003-add-user-auth.md

Example:
  .orc/agent_tools/share/get_task.py 0003-add-user-auth.md

Prints the task markdown content followed by any comments (the conversation
between agents attached to the task).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="get_task.py",
        description="Fetch task file content and conversation from the coordination server.",
    )
    parser.add_argument("task_filename", help="Task filename, e.g. 0003-add-user-auth.md")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from _orc_client import get_client  # noqa: PLC0415

    with get_client() as client:
        # Fetch raw markdown content.
        content_resp = client.get(f"/board/tasks/{args.task_filename}/content")
        if content_resp.status_code == 404:
            print(f"Error: task {args.task_filename!r} not found", file=sys.stderr)  # noqa: T201
            sys.exit(1)
        content_resp.raise_for_status()
        content = content_resp.json()["content"]

        # Fetch board entry for metadata and comments.
        entry_resp = client.get(f"/board/tasks/{args.task_filename}")
        entry_resp.raise_for_status()
        entry = entry_resp.json()

    print(content)  # noqa: T201

    comments = entry.get("comments", [])
    if comments:
        print("\n---\n## Conversation\n")  # noqa: T201
        for c in comments:
            author = c.get("from", "unknown")
            ts = c.get("ts", "")
            text = c.get("text", "")
            header = f"**{author}**" + (f" _{ts}_" if ts else "")
            print(f"{header}\n{text}\n")  # noqa: T201


if __name__ == "__main__":
    main()
