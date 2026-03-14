#!/usr/bin/env python3
"""get_vision.py — fetch vision file content from the coordination server.

Usage:
  .orc/agent_tools/planner/get_vision.py <vision-filename>

Arguments:
  vision-filename  The vision filename, e.g. 0007-orc-status-board-view.md

Example:
  .orc/agent_tools/planner/get_vision.py 0007-orc-status-board-view.md

Prints the full text of the vision file to stdout.

IMPORTANT: This tool MUST be run inside ``orc run``. Direct filesystem
access to ``.orc/`` is forbidden — use this script instead.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="get_vision.py",
        description="Fetch vision file content from the coordination server.",
    )
    parser.add_argument("vision_filename", help="Vision filename, e.g. 0007-foo.md")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from _orc_client import get_client  # noqa: PLC0415

    with get_client() as client:
        resp = client.get(f"/visions/{args.vision_filename}")
        if resp.status_code == 404:
            print(f"Error: vision {args.vision_filename!r} not found", file=sys.stderr)  # noqa: T201
            sys.exit(1)
        resp.raise_for_status()
        print(resp.json()["content"])  # noqa: T201


if __name__ == "__main__":
    main()
