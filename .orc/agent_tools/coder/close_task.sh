#!/usr/bin/env bash
# close_task.sh — signal that you have finished implementing a task.
#
# Usage:
#   .orc/agent_tools/coder/close_task.sh <agent-id> <task-code> "<message>"
#
# Arguments:
#   agent-id    Your agent identifier, e.g. coder-1
#   task-code   Zero-padded 4-digit task number, e.g. 0002
#   message     One-line summary of what was done
#
# Example:
#   .orc/agent_tools/coder/close_task.sh coder-1 0002 "implemented auth module; all tests green"
#
# This stages all changes (including new untracked files) and commits them,
# producing a commit of the form:
#   chore(coder-1.done.0002): implemented auth module; all tests green
#
# The orchestrator reads this prefix to know the coder is done and routes
# the task to QA.

set -euo pipefail

AGENT_ID="${1:-}"
TASK_CODE="${2:-}"
MESSAGE="${3:-}"

if [[ -z "$AGENT_ID" || -z "$TASK_CODE" || -z "$MESSAGE" ]]; then
    echo "Usage: close_task.sh <agent-id> <task-code> \"<message>\"" >&2
    echo "  e.g. close_task.sh coder-1 0002 \"finished implementation; tests green\"" >&2
    exit 1
fi

git add -A
git commit --allow-empty -m "chore(${AGENT_ID}.done.${TASK_CODE}): ${MESSAGE}"
