#!/usr/bin/env bash
# close_vision.sh — close a completed vision and log it to the changelog.
#
# Usage:
#   .orc/agent_tools/planner/close_vision.sh <vision-file> "<summary>" [task-file...]
#
# Arguments:
#   vision-file   Path to the vision markdown, e.g. .orc/vision/0001-shark-fleet.md
#   summary       2-4 sentence summary of what the vision described (quoted string)
#   task-file     Optional task filenames that implemented this vision
#
# Example:
#   .orc/agent_tools/planner/close_vision.sh .orc/vision/0001-shark-fleet.md \
#     "Implement distributed task processing using gRPC. Added worker pool management." \
#     .orc/work/0001-grpc-transport.md .orc/work/0002-worker-pool.md
#
# This script:
# 1. Derives the vision name from the filename
# 2. Gets the current timestamp in ISO 8601 format
# 3. Appends an entry to .orc/orc-CHANGELOG.md
# 4. Deletes the vision file
# 5. Prints a confirmation message

set -euo pipefail

VISION_FILE="${1:-}"
SUMMARY="${2:-}"
shift 2
TASK_FILES=("$@")

if [[ -z "$VISION_FILE" || -z "$SUMMARY" ]]; then
    echo "Usage: close_vision.sh <vision-file> \"<summary>\" [task-file...]" >&2
    echo "  e.g. close_vision.sh .orc/vision/0001-shark-fleet.md \"Description of vision.\" .orc/work/0001-task.md" >&2
    exit 1
fi

if [[ ! -f "$VISION_FILE" ]]; then
    echo "Error: vision file not found at $VISION_FILE" >&2
    exit 1
fi

# Derive vision name from filename (strip path and .md extension)
VISION_NAME="$(basename "$VISION_FILE" .md)"

# Get current timestamp in ISO 8601 format
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

CHANGELOG_FILE=".orc/orc-CHANGELOG.md"

# Format the implemented-by section
IMPLEMENTED_BY=""
if [[ ${#TASK_FILES[@]} -eq 0 ]]; then
    IMPLEMENTED_BY="—"
else
    # Join task files with ", " separator
    IMPLEMENTED_BY=$(IFS=", "; echo "${TASK_FILES[*]}")
fi

# Append to changelog
cat >> "$CHANGELOG_FILE" << EOF

## ${VISION_NAME} (closed ${TIMESTAMP})

${SUMMARY}

**Implemented by:** ${IMPLEMENTED_BY}
EOF

# Delete the vision file
rm "$VISION_FILE"

# Print confirmation
echo "Closed vision: $VISION_NAME"
echo "Updated changelog: $CHANGELOG_FILE"
