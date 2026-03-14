#!/usr/bin/env python3
"""close_vision.py — close a completed vision and log it to the changelog.

Usage:
  .orc/agent_tools/planner/close_vision.py <vision-file> "<summary>" [task-name...]

Arguments:
  vision-file   Full path (or just filename) of the vision markdown in .orc/vision/
  summary       2-4 sentence summary of what the vision described (quoted string)
  task-name     Optional task names that implemented this vision

Example:
  .orc/agent_tools/planner/close_vision.py \\
    .orc/vision/0001-shark-fleet.md \\
    "Implement distributed task processing using gRPC. Added worker pool management." \\
    0001-grpc-transport 0002-worker-pool

This script calls the orc coordination API to:
1. Append an entry to orc-CHANGELOG.md
2. Delete the vision file from .orc/vision/
3. Print a confirmation message

IMPORTANT: This tool MUST be run inside ``orc run``. Direct filesystem
access to ``.orc/`` is forbidden — use this script instead.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="close_vision.py",
        description="Close a completed vision.",
    )
    parser.add_argument("vision_file", help="Path to the vision markdown")
    parser.add_argument("summary", help="2-4 sentence summary of the vision")
    parser.add_argument("task_files", nargs="*", help="Task filenames that implemented this vision")
    args = parser.parse_args()

    vision_name = Path(args.vision_file).name

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from _orc_client import get_client  # noqa: PLC0415

    with get_client() as client:
        resp = client.post(
            f"/visions/{vision_name}/close",
            json={"summary": args.summary, "task_files": args.task_files},
        )
        resp.raise_for_status()

    print(f"Closed vision: {Path(vision_name).stem}")  # noqa: T201


if __name__ == "__main__":
    main()
