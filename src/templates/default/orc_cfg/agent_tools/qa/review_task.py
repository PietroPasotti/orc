#!/usr/bin/env python3
"""review_task.py — signal the outcome of a QA review (done or in-progress).

Usage:
  .orc/agent_tools/qa/review_task.py <agent-id> <task-code> done|in-progress "<message>"

Arguments:
  agent-id    Your agent identifier, e.g. qa-1
  task-code   Zero-padded 4-digit task number, e.g. 0002
  outcome     One of: done (approved), in-progress (rejected — back to coder)
  message     One-line summary of the review outcome

Examples:
  .orc/agent_tools/qa/review_task.py qa-1 0002 done "all tests green; no issues"
  .orc/agent_tools/qa/review_task.py qa-2 0003 in-progress "missing tests for error paths"
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="review_task.py",
        description="Signal the outcome of a QA review.",
    )
    parser.add_argument("agent_id", help="Your agent identifier, e.g. qa-1")
    parser.add_argument("task_code", help="Zero-padded 4-digit task number, e.g. 0002")
    parser.add_argument(
        "outcome",
        choices=["done", "in-progress"],
        help="Review outcome: 'done' (approved) or 'in-progress' (rejected, back to coder)",
    )
    parser.add_argument("message", help="One-line summary of the review outcome")
    args = parser.parse_args()

    verb = "approved" if args.outcome == "done" else "rejected"
    commit_msg = f"chore(qa/{args.task_code}): {verb} — {args.message}"
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": args.agent_id,
        "GIT_AUTHOR_EMAIL": f"{args.agent_id}@orc.local",
        "GIT_COMMITTER_NAME": args.agent_id,
        "GIT_COMMITTER_EMAIL": f"{args.agent_id}@orc.local",
    }
    result = subprocess.run(["git", "commit", "-a", "--allow-empty", "-m", commit_msg], env=env)
    if result.returncode != 0:
        sys.exit(result.returncode)

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
        resp = client.put(f"/board/tasks/{task_name}/status", json={"status": args.outcome})
        resp.raise_for_status()
        if args.outcome == "in-progress":
            resp = client.post(
                f"/board/tasks/{task_name}/comments",
                json={"author": args.agent_id, "text": args.message},
            )
            resp.raise_for_status()


if __name__ == "__main__":
    main()
