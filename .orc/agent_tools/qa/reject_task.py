#!/usr/bin/env python3
"""reject_task.py — deprecated wrapper; use review_task.py instead.

Usage:
  .orc/agent_tools/qa/reject_task.py <agent-id> <task-code> "<message>"

This script delegates to ``review_task.py rejected``.  Prefer using
``review_task.py`` directly with an explicit outcome argument.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 4:  # noqa: PLR2004
        print(  # noqa: T201
            "Usage: reject_task.py <agent-id> <task-code> <message>",
            file=sys.stderr,
        )
        sys.exit(1)
    agent_id, task_code, message = sys.argv[1], sys.argv[2], sys.argv[3]
    review = Path(__file__).parent / "review_task.py"
    import subprocess  # noqa: PLC0415

    result = subprocess.run(
        [sys.executable, str(review), agent_id, task_code, "in-progress", message]
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
