#!/usr/bin/env bash
# publish_task.sh — commit a newly created task file (and updated board) to dev.
#
# Usage:
#   .orc/agent_tools/planner/publish_task.sh <task-file> [extra-files...]
#
# Arguments:
#   task-file     Path to the new task markdown, e.g. orc/work/0003-add-foo.md
#   extra-files   Optional additional files to stage (e.g. orc/work/board.yaml)
#
# Example:
#   .orc/agent_tools/planner/publish_task.sh orc/work/0003-add-foo.md orc/work/board.yaml
#
# This stages the given files and produces a commit of the form:
#   chore(orc): add task 0003-add-foo
#
# All git commands must be run from inside the dev worktree.

set -euo pipefail

TASK_FILE="${1:-}"

if [[ -z "$TASK_FILE" ]]; then
    echo "Usage: publish_task.sh <task-file> [extra-files...]" >&2
    echo "  e.g. publish_task.sh orc/work/0003-add-foo.md orc/work/board.yaml" >&2
    exit 1
fi

# Derive a clean task name from the filename (strip leading path and .md)
TASK_NAME="$(basename "$TASK_FILE" .md)"

# Stage the task file plus any extra files passed as arguments
git add "$@"

git commit -m "chore(orc): add task ${TASK_NAME}"
