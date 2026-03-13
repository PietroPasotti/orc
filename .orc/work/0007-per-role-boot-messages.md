# Task 0007 – Per-role custom boot messages

## Overview

Two related `#TODO` comments request that each agent role emit a
role-specific boot message instead of the current generic one that simply
lists open tasks.  This improves Telegram observability: it is immediately
clear from the boot message what the agent intends to work on.

## Scope

**In scope:**
- Update `_boot_message_body()` in `src/orc/engine/context.py` to accept
  the agent ID and return a role-aware body:
  - `planner-N`: `"planning vision {vision-name}" ` or `"translating
    TODOs/FIXMEs"` or `"no open tasks on board."` (current fallback)
  - `coder-N`: `"picking up work/{task-name}"` (already done in some
    places ad-hoc; make it canonical)
  - `qa-N`: `"reviewing feat/{task-name}"`
  - Default fallback: current behaviour (list open tasks)
- Update `_post_boot_message()` in `src/orc/engine/workflow.py` to call
  the updated `_boot_message_body(agent_id)` instead of the old signature.
- Remove the `# TODO` comments from both files once the work is done.
- Add / update unit tests in `tests/test_context.py` and
  `tests/test_workflow.py` to cover the new per-role bodies.

**Out of scope:**
- Changes to how the Telegram message is formatted or sent.
- Adding a new Telegram message type or state.
- Any TUI changes.

## Steps

- [ ] 1. **`src/orc/engine/context.py`** – update `_boot_message_body()`:
  - Change signature to `_boot_message_body(agent_id: str) -> str`.
  - Parse the role from `agent_id` (e.g. `"coder-1"` → role `"coder"`).
  - For **planner**: read the first open task name from the board; if
    present return `"planning {task-name}"`.  If the board has no open
    tasks but `board.get("visions")` is non-empty, return `"translating
    vision docs"`.  Otherwise return `"no open tasks on board."`.
  - For **coder**: read the first open task; return `"picking up
    work/{task-name}"` or `"no open tasks on board."`.
  - For **qa**: read the first open task; return `"reviewing
    feat/{task-name}"` or `"no open tasks on board."`.
  - Default: current fallback (list open tasks by name).
  - Remove the `# TODO` comment (line 508).

- [ ] 2. **`src/orc/engine/workflow.py`** – update `_post_boot_message()`:
  - Change the call to `_boot_message_body()` to pass `agent_id`.
  - Remove the `# TODO` comment (line 67).

- [ ] 3. **`tests/test_context.py`** – add tests:
  - `test_boot_message_body_planner_with_open_task()`: board has open task →
    body starts with `"planning "`.
  - `test_boot_message_body_coder_with_open_task()`: body contains
    `"picking up work/"`.
  - `test_boot_message_body_qa_with_open_task()`: body contains
    `"reviewing feat/"`.
  - `test_boot_message_body_no_tasks()`: all roles → body is
    `"no open tasks on board."`.

- [ ] 4. **`tests/test_workflow.py`** – ensure existing `_post_boot_message`
  tests still pass; add a test that the body changes per role.

- [ ] 5. Run `just test` and `just lint`; fix any failures.

- [ ] 6. Commit:
  ```
  feat(engine): per-role custom boot messages
  ```

## Notes

- Sources: `src/orc/engine/context.py:508` and
  `src/orc/engine/workflow.py:67`.
- The `workflow.py` comment includes example strings for each role — follow
  those examples:
  - planner: `"Starting to refine vision NNNN-....md"`
  - coder-N: `"Starting work on feat/NNNN-..."`
  - qa-N: `"Starting to review feat/NNNN-..."`
  Adapt these to match what the board actually contains at runtime.
