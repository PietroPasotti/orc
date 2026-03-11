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
# Bump level: patch (default), minor, or major. Pass nowait=true to skip polling.
#   just release "fix: correct widget sizing"
#   just release "feat: add export command" minor
#   just release "feat!: redesign API" major
#   just release "fix: typo" patch nowait=true
release message bump="patch" nowait="false":
    #!/usr/bin/env bash
    set -euo pipefail

    echo "Running tests before release…"
    just test
    echo "✓ Tests passed"

    latest=$(git tag --sort=-version:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -1)
    if [[ -z "$latest" ]]; then
        echo "No existing vX.Y.Z tag found — defaulting to v0.0.0 as base." >&2
        latest="v0.0.0"
    fi

    IFS='.' read -r major minor patch <<< "${latest#v}"

    case "{{ bump }}" in
        major) major=$((major + 1)); minor=0; patch=0 ;;
        minor) minor=$((minor + 1)); patch=0 ;;
        patch) patch=$((patch + 1)) ;;
        *) echo "Unknown bump level: {{ bump }} (use patch, minor, or major)" >&2; exit 1 ;;
    esac

    new_tag="v${major}.${minor}.${patch}"
    echo "Tagging $latest → $new_tag"
    git tag -a "$new_tag" -m "{{ message }}"
    git push origin "$new_tag"
    echo "✓ Pushed $new_tag"

    if [[ "{{ nowait }}" == "true" ]]; then
        echo "Skipping CI poll (nowait=true)."
        exit 0
    fi

    just _poll-release "$new_tag"

# Force-update the current release tag to re-trigger the CI pipeline.
#   just re-release "fix: retry release"
#   just re-release "fix: retry release" nowait=true
re-release message nowait="false":
    #!/usr/bin/env bash
    set -euo pipefail

    echo "Running tests before release…"
    just test
    echo "✓ Tests passed"

    latest=$(git tag --sort=-version:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -1)
    if [[ -z "$latest" ]]; then
        echo "No existing release tag found." >&2; exit 1
    fi

    echo "Force-updating tag $latest"
    git tag -fa "$latest" -m "{{ message }}"
    git push --force origin "$latest"
    echo "✓ Force-pushed $latest"

    if [[ "{{ nowait }}" == "true" ]]; then
        echo "Skipping CI poll (nowait=true)."
        exit 0
    fi

    just _poll-release "$latest"

# (internal) Poll GitHub Actions for a release run on the given tag.
_poll-release tag:
    #!/usr/bin/env bash
    set -euo pipefail

    echo "Polling CI release pipeline for {{ tag }}…"
    sleep 5
    run_id=""
    for i in $(seq 1 12); do
        run_id=$(gh run list --workflow=ci.yml --branch="{{ tag }}" --limit=5 --json databaseId,event \
            | python3 -c "import sys,json; runs=json.load(sys.stdin); [print(r['databaseId']) for r in runs if r.get('event')=='push']" \
            | head -1 2>/dev/null || true)
        if [[ -n "$run_id" ]]; then break; fi
        echo "  waiting for run to appear… (${i}/12)"
        sleep 5
    done

    if [[ -z "$run_id" ]]; then
        repo=$(gh repo view --json nameWithOwner -q .nameWithOwner)
        echo "✗ Could not find a CI run for {{ tag }} after 60 s. Check https://github.com/${repo}/actions" >&2
        exit 1
    fi

    echo "  run id: $run_id — watching…"
    gh run watch "$run_id" --exit-status
    conclusion=$(gh run view "$run_id" --json conclusion -q .conclusion)
    if [[ "$conclusion" == "success" ]]; then
        echo "✓ Release pipeline succeeded for {{ tag }}."
    else
        echo "✗ Release pipeline ended with conclusion: $conclusion" >&2
        exit 1
    fi
