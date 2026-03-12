#!/usr/bin/env bash
# approve_task.sh — signal that the task passed QA review.
#
# Usage:
#   .orc/agent_tools/qa/approve_task.sh <agent-id> <task-code> "<message>"
#
# Arguments:
#   agent-id    Your agent identifier, e.g. qa-2
#   task-code   Zero-padded 4-digit task number, e.g. 0002
#   message     One-line summary of the review outcome
#
# Example:
#   .orc/agent_tools/qa/approve_task.sh qa-1 0002 "all tests green; no critical issues"
#
# This commits ALL staged and unstaged tracked changes (git commit -a) and
# produces a commit of the form:
#   chore(qa-1.approve.0002): all tests green; no critical issues
#
# The orchestrator reads this prefix to trigger an automatic merge into dev.

set -euo pipefail

AGENT_ID="${1:-}"
TASK_CODE="${2:-}"
MESSAGE="${3:-}"

if [[ -z "$AGENT_ID" || -z "$TASK_CODE" || -z "$MESSAGE" ]]; then
    echo "Usage: approve_task.sh <agent-id> <task-code> \"<message>\"" >&2
    echo "  e.g. approve_task.sh qa-1 0002 \"all tests green; no critical issues\"" >&2
    exit 1
fi

git commit -a --allow-empty -m "chore(${AGENT_ID}.approve.${TASK_CODE}): ${MESSAGE}"
