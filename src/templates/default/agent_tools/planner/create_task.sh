#!/usr/bin/env bash
# create_task.sh — create a new task and add it to the kanban board.
#
# Usage:
#   .orc/agent_tools/planner/create_task.sh <task-title>
#
# Arguments:
#   task-title  Short dash-separated title, e.g. add-user-auth
#
# Example:
#   .orc/agent_tools/planner/create_task.sh add-user-auth
#
# This script:
# 1. Reads the current counter from .orc/work/board.yaml
# 2. Formats the task ID as a 4-digit zero-padded string (e.g. 0005)
# 3. Creates .orc/work/${TASK_ID}-${TASK_TITLE}.md from template
# 4. Adds the task filename to the 'open' list in board.yaml
# 5. Increments the counter and writes it back
# 6. Prints the created filename to stdout
#
# The created task file should be edited to fill in the overview, scope,
# steps, and notes before publishing with publish_task.sh.

set -euo pipefail

TASK_TITLE="${1:-}"

if [[ -z "$TASK_TITLE" ]]; then
    echo "Usage: create_task.sh <task-title>" >&2
    echo "  e.g. create_task.sh add-user-auth" >&2
    exit 1
fi

BOARD_FILE=".orc/work/board.yaml"

if [[ ! -f "$BOARD_FILE" ]]; then
    echo "Error: board file not found at $BOARD_FILE" >&2
    exit 1
fi

# Read the current counter value from board.yaml
# Expecting a line like: counter: 4
COUNTER=$(grep "^counter:" "$BOARD_FILE" | awk '{print $2}')

if [[ -z "$COUNTER" ]]; then
    echo "Error: could not read counter from $BOARD_FILE" >&2
    exit 1
fi

# Format task ID as 4-digit zero-padded string
TASK_ID=$(printf "%04d" "$COUNTER")
TASK_FILE=".orc/work/${TASK_ID}-${TASK_TITLE}.md"

# Create the task file from template
cat > "$TASK_FILE" << EOF
# ${TASK_ID}-${TASK_TITLE}

## Overview

<!-- What and why -->

## Scope

**In scope:**
- 

**Out of scope:**
- 

## Steps

- [ ] 

## Notes

EOF

# Update board.yaml: add task to 'open' list and increment counter
# We need to insert the new task into the open list
# Read the file, find the 'open:' line, and insert the new task

# Create a temporary file
TEMP_FILE="${BOARD_FILE}.tmp"

# Process the board.yaml file
{
    while IFS= read -r line; do
        if [[ "$line" == "open:" ]]; then
            echo "open:"
            # Read and print all existing open tasks
            while IFS= read -r task_line; do
                if [[ "$task_line" =~ ^-[[:space:]] || "$task_line" =~ ^[[:space:]]+name: ]]; then
                    echo "$task_line"
                else
                    # We've reached the next section
                    echo "$task_line"
                    break
                fi
            done
            # Add the new task
            echo "- name: ${TASK_ID}-${TASK_TITLE}.md"
        elif [[ "$line" == "counter:"* ]]; then
            # Increment counter
            NEW_COUNTER=$((COUNTER + 1))
            echo "counter: $NEW_COUNTER"
        else
            echo "$line"
        fi
    done < "$BOARD_FILE"
} > "$TEMP_FILE"

mv "$TEMP_FILE" "$BOARD_FILE"

# Print the created filename
echo "$TASK_FILE"
