# Default: list recipes
default:
    @just --list

# Install dependencies and git hooks
install:
    uv sync --all-groups
    pre-commit install --install-hooks
    pre-commit install --hook-type commit-msg

# Run the orchestrator
# Examples:
#   just run                    # default squad, 1 invocation
#   just run --maxloops 0       # run until complete
#   just run --squad broad      # use src/.orc/squads/broad.yaml
#   just run --dry-run          # print context without invoking
run *args:
    uv run orc --config-dir src run {{args}}

# Print current workflow state
status:
    uv run orc --config-dir src status

# Rebase dev on main and merge
merge:
    uv run orc --config-dir src merge

# Run the test suite
test:
    uv run pytest tests/ -v

# Run tests with default options (includes coverage if configured)
test-ci:
    uv run pytest tests/

# Lint only (no changes)
lint:
    uv run ruff check src/ tests/

# Auto-fix lint and format
fmt:
    uv run ruff check --fix src/ tests/
    uv run ruff format src/ tests/

# Run all pre-commit hooks against every file
hooks:
    pre-commit run --all-files

# Interactive conventional commit prompt
commit:
    uv run cz commit

# Bump version and update CHANGELOG (maintainers only)
bump:
    uv run cz bump
