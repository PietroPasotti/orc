#!/usr/bin/env python3
"""approve_task.py — deprecated wrapper; use review_task.py instead.

Usage:
  .orc/agent_tools/qa/approve_task.py <agent-id> <task-code> "<message>"

This script delegates to ``review_task.py approved``.  Prefer using
``review_task.py`` directly with an explicit outcome argument.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 4:  # noqa: PLR2004
        print(  # noqa: T201
            "Usage: approve_task.py <agent-id> <task-code> <message>",
            file=sys.stderr,
        )
        sys.exit(1)
    agent_id, task_code, message = sys.argv[1], sys.argv[2], sys.argv[3]
    review = Path(__file__).parent / "review_task.py"
    import subprocess  # noqa: PLC0415

    result = subprocess.run([sys.executable, str(review), agent_id, task_code, "done", message])
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
