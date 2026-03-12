## Manage the kanban board

`orc/work/board.yaml` is the single source of truth for the backlog.

**When creating a new task:**

1. Read `board.yaml` and note the `counter` value (e.g. `3`).
2. Format the task ID as a 4-digit zero-padded string: `f"{counter:04d}"` → `"0003"`.
3. Create the task file at `orc/work/0003-short-title.md`.
4. Add the filename to the `open` list in `board.yaml`.
5. Increment `counter` by 1 and write it back.

Example `board.yaml` after adding task 0003:

```yaml
counter: 4

open:
  - name: 0002-module-combat-stats.md
  - name: 0003-short-title.md

done:
  - name: 0001-sample-plan.md
    commit-tag: example
    timestamp: 2026-03-01T00:00:00Z
```
