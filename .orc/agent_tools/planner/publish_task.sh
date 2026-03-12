#!/usr/bin/env bash
# publish_task.sh — commit a newly created task file (and updated board) to dev.
#
# Usage:
#   .orc/agent_tools/planner/publish_task.sh <agent-id> <task-file> [extra-files...]
#
# Arguments:
#   agent-id    Your agent identifier, e.g. planner-1
#   task-file   Path to the new task markdown, e.g. .orc/work/0003-add-foo.md
#   extra-files Optional additional files to stage (e.g. .orc/work/board.yaml)
#
# Example:
#   .orc/agent_tools/planner/publish_task.sh planner-1 .orc/work/0003-add-foo.md .orc/work/board.yaml
#
# This stages the given files and produces a structured exit commit:
#   chore(planner-1.ready.0003): add task 0003-add-foo
#
# The commit format follows the same chore(<agent-id>.<action>.<task-code>)
# convention used by coder and qa tools, allowing the git history to be
# inspected uniformly across all agent roles.
#
# All git commands must be run from inside the dev worktree.

set -euo pipefail

AGENT_ID="${1:-}"
TASK_FILE="${2:-}"

if [[ -z "$AGENT_ID" || -z "$TASK_FILE" ]]; then
    echo "Usage: publish_task.sh <agent-id> <task-file> [extra-files...]" >&2
    echo "  e.g. publish_task.sh planner-1 .orc/work/0003-add-foo.md .orc/work/board.yaml" >&2
    exit 1
fi

# Derive task name and 4-digit code from the filename (strip path and .md)
TASK_NAME="$(basename "$TASK_FILE" .md)"
TASK_CODE="${TASK_NAME:0:4}"

# Stage the task file plus any extra files passed as arguments (skip agent-id)
shift
git add "$@"

git commit -m "chore(${AGENT_ID}.ready.${TASK_CODE}): add task ${TASK_NAME}"
