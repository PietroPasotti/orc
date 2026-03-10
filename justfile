# Default: list recipes
default:
    @just --list

# Run the orchestrator
# Examples:
#   just run                    # default squad
#   just run --maxloops 0       # run until complete
#   just run --squad broad      # use orc/squads/broad.yaml
run *args:
    uv run orc run {{args}}

# Print current workflow state
status:
    uv run orc status

# Run tests
test:
    uv run pytest tests/ -v

# Rebase dev on main and merge
merge:
    uv run orc merge
