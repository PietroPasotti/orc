# Install dependencies and git hooks
install:
    uv sync --all-groups
    pre-commit install --install-hooks
    pre-commit install --hook-type commit-msg

# Run the test suite
test:
    uv run pytest tests/ -v

# Lint only (no changes)
lint:
    uv run ruff check src/ tests/

# Auto-fix lint and format
fmt:
    uv run ruff check --fix src/ tests/
    uv run ruff format src/ tests/
