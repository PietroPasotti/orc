# 0002 – Configurable log folder & `just logs` recipe

## Overview

Two related TODOs in the codebase request improvements to orc's log management:

1. **`src/orc/logger.py:28`** – The log folder is currently hardcoded to
   `~/.cache/orc/`. A new `ORC_LOG_DIR` environment variable should let users
   redirect *all* orc logs (orchestrator + agent subprocesses) to a
   project-local directory (e.g. `./orc/`) without having to set individual
   file paths.

2. **`src/templates/default/justfile:9`** – A `just logs` recipe should be
   added to the default bundled justfile (and to the project's `.orc/justfile`)
   so users can easily tail or print orc/agent log files.

## Scope

**In scope:**
- Add `ORC_LOG_DIR` env-var support to `src/orc/logger.py`:
  - When set, `ORC_LOG_FILE` defaults to `$ORC_LOG_DIR/orc.log` instead of
    `~/.cache/orc/orc.log`.
  - `ORC_LOG_FILE` still takes precedence over `ORC_LOG_DIR` when both are set.
  - Document the new variable in the module docstring and in `README.md`'s
    environment-variable table.
- Add a `logs` recipe to `src/templates/default/justfile`:
  - Signature: `logs path='' tail='false' agent='all'`
  - Resolves the log directory (default: `~/.cache/orc`; overridable by
    `--path`).
  - `--agent all` (default): prints/tails the orchestrator log plus all agent
    logs.
  - `--agent orc`: orchestrator log only.
  - `--agent <name>`: a single named agent log (e.g. `coder-1`).
  - `--tail`: use `tail -f` instead of `cat`.
- Mirror the same recipe in `.orc/justfile` (the project's own justfile used
  during development of orc itself).
- Remove the `# TODO` comment from `src/orc/logger.py:28` once the work is done.
- Remove the `# TODO` comment from `src/templates/default/justfile:9` once the
  work is done.
- Add / update unit tests for the `ORC_LOG_DIR` logic in `tests/test_logger.py`
  (create the file if it does not exist).

**Out of scope:**
- Agent-level log files (the agent subprocess stdout/stderr routing is a
  separate concern; the `just logs` recipe should work with whatever log files
  already exist in the log directory).
- Any UI/TUI changes.

## Steps

- [ ] 1. **`src/orc/logger.py`** – add `ORC_LOG_DIR` support:
  - After resolving `ORC_LOG_FILE` from the environment, if the resolved path
    is still the default (`_DEFAULT_LOG_FILE`) *and* `ORC_LOG_DIR` is set,
    override `resolved_log_file` to `Path(os.environ["ORC_LOG_DIR"]) / "orc.log"`.
  - Update the module docstring to document `ORC_LOG_DIR`.
  - Delete the `# TODO` comment on line 28.

- [ ] 2. **`README.md`** – add `ORC_LOG_DIR` row to the environment-variable
  table (below the `ORC_LOG_FILE` row):
  ```
  | `ORC_LOG_DIR` | No | Override the log *folder*. Sets `ORC_LOG_FILE` to `$ORC_LOG_DIR/orc.log` when `ORC_LOG_FILE` is not set. |
  ```

- [ ] 3. **`src/templates/default/justfile`** – replace the `# TODO` block
  (lines 9–15) with the implemented `logs` recipe:
  ```just
  # Print or tail orc log files.
  # Options:
  #   --path <dir>   Log directory (default: ~/.cache/orc)
  #   --agent <name> all | orc | <agent-name>  (default: all)
  #   --tail         Follow the log(s) with tail -f
  logs *args:
      uv run orc --project-dir {{repo_root}} logs {{args}}
  ```
  (The `orc logs` CLI command needs to be implemented too — see note below.)

- [ ] 4. **`src/orc/main.py`** – add an `orc logs` CLI command:
  - Parameters: `--path` (default `~/.cache/orc`), `--agent` (default `all`),
    `--tail` flag.
  - For `--agent all`: collect `orc.log` and any `*.log` files in the
    directory; print or `tail -f` them.
  - For `--agent orc`: just `orc.log`.
  - For `--agent <name>`: just `<name>.log`.

- [ ] 5. **`.orc/justfile`** – add the same `logs` recipe (mirroring step 3).
  Delete the `# TODO` comment there if present.

- [ ] 6. **Tests** – add / update `tests/test_logger.py`:
  - Test that when `ORC_LOG_DIR` is set and `ORC_LOG_FILE` is not, the log file
    resolves to `$ORC_LOG_DIR/orc.log`.
  - Test that `ORC_LOG_FILE` still takes precedence over `ORC_LOG_DIR`.
  - Test that when neither is set, the default (`~/.cache/orc/orc.log`) is used.

- [ ] 7. Run `just test` and `just lint`; fix any failures.

- [ ] 8. Commit with:
  ```
  feat(logger): add ORC_LOG_DIR env var and just logs recipe
  ```

## Notes

- The `just logs` recipe delegates to `orc logs` (a new CLI subcommand). This
  keeps the justfile thin and places the logic in Python where it is testable.
- Agent-specific log file names (e.g. `coder-1.log`) are not yet produced by
  orc; the recipe should gracefully handle a missing file (print a warning and
  skip it) rather than hard-failing.
- Sources:
  - `src/orc/logger.py` line 28 (`# TODO`)
  - `src/templates/default/justfile` lines 9–15 (`# TODO`)
