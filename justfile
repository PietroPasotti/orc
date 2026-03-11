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

# Create and push a new release tag, then poll the CI release pipeline.
# Bump level: patch (default), minor, or major. Pass --nowait to skip polling.
#   just release "fix: correct widget sizing"
#   just release "feat: add export command" --minor
#   just release "feat!: redesign API" --major
#   just release "fix: typo" --nowait
release message *flags:
    #!/usr/bin/env bash
    set -euo pipefail

    bump="patch"
    nowait=false
    for arg in {{ flags }}; do
        case "$arg" in
            --minor) bump="minor" ;;
            --major) bump="major" ;;
            --nowait) nowait=true ;;
            *) echo "Unknown argument: $arg" >&2; exit 1 ;;
        esac
    done

    latest=$(git tag --sort=-version:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -1)
    if [[ -z "$latest" ]]; then
        echo "No existing vX.Y.Z tag found — defaulting to v0.0.0 as base." >&2
        latest="v0.0.0"
    fi

    IFS='.' read -r major minor patch <<< "${latest#v}"

    case "$bump" in
        major) major=$((major + 1)); minor=0; patch=0 ;;
        minor) minor=$((minor + 1)); patch=0 ;;
        patch) patch=$((patch + 1)) ;;
    esac

    new_tag="v${major}.${minor}.${patch}"
    echo "Tagging $latest → $new_tag"

    git tag -a "$new_tag" -m "{{ message }}"
    git push origin "$new_tag"
    echo "✓ Pushed $new_tag"

    if $nowait; then
        echo "Skipping CI poll (--nowait)."
        exit 0
    fi

    echo "Polling CI release pipeline for $new_tag…"
    sleep 5
    run_id=""
    for i in $(seq 1 12); do
        run_id=$(gh run list --workflow=ci.yml --branch="$new_tag" --limit=5 --json databaseId,event \
            | python3 -c "import sys,json; runs=json.load(sys.stdin); [print(r['databaseId']) for r in runs if r.get('event')=='push']" \
            | head -1 2>/dev/null || true)
        if [[ -n "$run_id" ]]; then break; fi
        echo "  waiting for run to appear… (${i}/12)"
        sleep 5
    done

    if [[ -z "$run_id" ]]; then
        repo=$(gh repo view --json nameWithOwner -q .nameWithOwner)
        echo "✗ Could not find a CI run for $new_tag after 60 s. Check https://github.com/${repo}/actions" >&2
        exit 1
    fi

    echo "  run id: $run_id — watching…"
    gh run watch "$run_id" --exit-status
    conclusion=$(gh run view "$run_id" --json conclusion -q .conclusion)
    if [[ "$conclusion" == "success" ]]; then
        echo "✓ Release pipeline succeeded for $new_tag."
    else
        echo "✗ Release pipeline ended with conclusion: $conclusion" >&2
        exit 1
    fi
