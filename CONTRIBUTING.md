# Contributing to orc

Thank you for contributing! This document explains the practical workflow every
contributor (human or AI) is expected to follow.

---

## First-time setup

```bash
# 1. Clone the repo
git clone https://github.com/your-org/orc.git
cd orc

# 2. Install dependencies + git hooks in one step
just install
```

`just install` runs:
- `uv sync --all-groups` — creates `.venv` and installs all runtime and dev
  dependencies.
- `pre-commit install --install-hooks` — wires up the lint/format hooks on
  `git commit`.
- `pre-commit install --hook-type commit-msg` — wires up the commit-message
  validation hook.

You only need to do this once per clone.

---

## The development loop (TDD)

orc follows a strict **test-first** workflow:

```
1. Write a failing test
       ↓
2. Run `just test` — confirm it fails for the right reason
       ↓
3. Write the minimum implementation to make it pass
       ↓
4. Run `just test` — confirm it passes
       ↓
5. Refactor freely — keep running `just test`
       ↓
6. Commit (see below)
```

Tests live in `tests/` and mirror the `src/orc/` package layout.
Tests must not make real network calls, spawn real subprocesses, or read
real `.env` files — the `conftest.py` stubs out `dotenv`, `httpx`, and
`subprocess.Popen` for you.

---

## Committing

Use the interactive prompt to author a correctly-formatted commit:

```bash
just commit        # launches cz commit
```

You will be guided through:

1. **Type** — `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `chore`, `style`
2. **Scope** — optional, e.g. `main`, `dispatcher`, `pool`, `squad`, `telegram`, `invoke`, `cli`, `adr`
3. **Short description** — imperative mood, lowercase, no trailing period
4. **Body** — optional, free-form
5. **Breaking change** — optional

**Examples of good commit messages:**

```
feat(dispatcher): add watchdog timeout per agent
fix(squad): reject planner > 1 in validation
docs(adr): add ADR-0002 squad architecture
test(main): cover bootstrap --force flag
chore: bump certifi to 2026.1.0
```

If you prefer to write the message yourself, raw `git commit -m "..."` is fine
— the commit-msg hook will validate the format and reject it if it doesn't
conform.

---

## Pre-commit hooks

The following hooks run automatically on every `git commit`:

| Hook | What it does |
|---|---|
| `ruff` | Lints and auto-fixes `src/` and `tests/` |
| `ruff-format` | Formats `src/` and `tests/` |
| `commitizen` | Validates the commit message format |

If Ruff auto-fixes something, the commit will be **aborted** so you can
review and `git add` the fixes, then commit again. This is intentional.

To run all hooks manually against every file (e.g. before a PR):

```bash
just hooks
```

---

## Other useful recipes

```bash
just              # list all recipes
just test         # run the test suite
just lint         # check only, no changes
just fmt          # auto-fix lint + format
just bump         # semver version bump + CHANGELOG update (maintainers only)
```

---

## Package layout

```
src/orc/
  main.py        CLI entry point (typer app; run, status, merge, bootstrap)
  dispatcher.py  Poll-based parallel agent scheduler
  pool.py        AgentPool — subprocess.Popen lifecycle management
  squad.py       Squad profile loader (orc/squads/*.yaml)
  telegram.py    Telegram send/receive helpers
  invoke.py      Agent invocation (copilot / claude backends)
  logger.py      structlog configuration
  roles/         Bundled generic role templates
  squads/        Bundled default squad profile
tests/
  conftest.py         Shared stubs (dotenv, httpx, FakePopen, make_msg)
  test_main.py        CLI commands, boot message, blocked-state recovery
  test_state_machine.py  State machine: determine_next_agent, _has_unresolved_block
  test_invoke.py      invoke_agent / spawn dispatching
  test_squad.py       Squad profile loading and validation
```

---

## Writing an ADR

For any significant architectural decision, write an ADR before implementing:

1. Copy the structure from an existing ADR in `docs/adr/`.
2. Number it sequentially (`NNNN-short-title.md`).
3. Add it to the index in `docs/adr/README.md`.
4. Commit it with type `docs` and scope `adr`:
   ```
   docs(adr): add ADR-0002 <title>
   ```

---

## Licence

By contributing you agree that your contributions will be licensed under the
project's MIT licence.
