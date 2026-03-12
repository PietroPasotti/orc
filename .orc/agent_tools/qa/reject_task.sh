#!/usr/bin/env bash
# reject_task.sh — signal that the task failed QA review.
#
# Usage:
#   .orc/agent_tools/qa/reject_task.sh <agent-id> <task-code> "<message>"
#
# Arguments:
#   agent-id    Your agent identifier, e.g. qa-2
#   task-code   Zero-padded 4-digit task number, e.g. 0003
#   message     One-line summary of the blocking issue
#
# Example:
#   .orc/agent_tools/qa/reject_task.sh qa-2 0003 "missing tests for error paths; see task file"
#
# Stage any feedback files (e.g. the updated task .md) BEFORE calling this
# script so they are included in the commit:
#   git add orc/work/0003-foo.md
#   .orc/agent_tools/qa/reject_task.sh qa-2 0003 "missing tests for error paths"
#
# This commits ALL staged and unstaged tracked changes (git commit -a) and
# produces a commit of the form:
#   chore(qa-2.reject.0003): missing tests for error paths; see task file
#
# The orchestrator reads this prefix to route the task back to a coder.

set -euo pipefail

AGENT_ID="${1:-}"
TASK_CODE="${2:-}"
MESSAGE="${3:-}"

if [[ -z "$AGENT_ID" || -z "$TASK_CODE" || -z "$MESSAGE" ]]; then
    echo "Usage: reject_task.sh <agent-id> <task-code> \"<message>\"" >&2
    echo "  e.g. reject_task.sh qa-2 0003 \"missing tests for error paths\"" >&2
    exit 1
fi

git commit -a --allow-empty -m "chore(${AGENT_ID}.reject.${TASK_CODE}): ${MESSAGE}"
