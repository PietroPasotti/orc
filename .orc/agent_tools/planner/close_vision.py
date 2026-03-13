#!/usr/bin/env python3
"""close_vision.py — close a completed vision and log it to the changelog.

Usage:
  .orc/agent_tools/planner/close_vision.py <vision-file> "<summary>" [task-name...]

Arguments:
  vision-file   Full path to the vision markdown in the project cache
  summary       2-4 sentence summary of what the vision described (quoted string)
  task-name     Optional task names that implemented this vision

Example:
  .orc/agent_tools/planner/close_vision.py \
    ~/.cache/orc/projects/<uuid>/vision/0001-shark-fleet.md \
    "Implement distributed task processing using gRPC. Added worker pool management." \
    0001-grpc-transport 0002-worker-pool

This script:
1. Derives the vision name from the filename
2. Gets the current timestamp in ISO 8601 format
3. Appends an entry to .orc/orc-CHANGELOG.md
4. Deletes the vision file from the project cache
5. Prints a confirmation message
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

CHANGELOG_FILE = Path(".orc/orc-CHANGELOG.md")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="close_vision.py",
        description="Close a completed vision and log it to the changelog.",
    )
    parser.add_argument("vision_file", help="Path to the vision markdown")
    parser.add_argument("summary", help="2-4 sentence summary of the vision")
    parser.add_argument("task_files", nargs="*", help="Task filenames that implemented this vision")
    args = parser.parse_args()

    vision_path = Path(args.vision_file)
    if not vision_path.exists():
        print(f"Error: vision file not found at {vision_path}", file=sys.stderr)  # noqa: T201
        sys.exit(1)

    vision_name = vision_path.stem
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    implemented_by = ", ".join(args.task_files) if args.task_files else "—"

    entry = (
        f"\n## {vision_name} (closed {timestamp})\n\n"
        f"{args.summary}\n\n"
        f"**Implemented by:** {implemented_by}\n"
    )

    with CHANGELOG_FILE.open("a") as f:
        f.write(entry)

    vision_path.unlink()

    print(f"Closed vision: {vision_name}")  # noqa: T201
    print(f"Updated changelog: {CHANGELOG_FILE}")  # noqa: T201


if __name__ == "__main__":
    main()
