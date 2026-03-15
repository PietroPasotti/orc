# ADR-0002 — Board-Backed State Machine

**Status**: Accepted

---

## Context

`orc` is a multi-agent orchestrator.  It decides which agent to invoke next
(planner, coder, or qa) by reading **board state** (a YAML file in the project
cache) and Telegram message history — no database, no in-process state.  Git
is used exclusively for branch/commit detection; the board is the single source
of truth for task status.  This document describes the state machine formally,
explains the design choices, and records where the formal model lives and how
its soundness is verified.

---

## Decision

Agent routing is determined by a **pure function** of observable state:

```
next_agent = f(board, git, telegram_messages)
```

The canonical implementation of this function is:

```
dispatcher._dispatch()
  └─ workflow.determine_next_agent(messages)
       ├─ workflow._has_unresolved_block(messages)   # Telegram scan
       └─ workflow._derive_task_state(task)          # board status + git queries
            ├─ board._active_task_name()             # board YAML (project cache)
            └─ board status field                    # planned/in-progress/in-review/done/blocked
```

A **formal model** (`state_machine.py`) mirrors this logic in pure Python and
is tested exhaustively for deadlock-freedom via BFS.

---

## Per-Task State Machine

### Inputs

| Input | Source | Notes |
|-------|--------|-------|
| `has_open_task` | board YAML `tasks:[]` | any task present in the list |
| `has_pending_vision` | board YAML `visions:[]` | any un-planned vision |
| `branch_exists` | `git branch --list <feat/NNNN-*>` | feature branch present |
| `commits_ahead` | `git rev-list <branch> ^main` | any commits not in main |
| `merged_into_dev` | `git merge-base --is-ancestor <branch> dev` | only checked when branch exists and has no commits ahead |
| `last_commit` | board `status` field via `_STATUS_TO_LAST_COMMIT` map | `in-review`→CODER_DONE, `done`→QA_PASSED, `in-progress`→QA_OTHER |
| `block` | newest non-boot Telegram message | `HARD`, `SOFT`, or `NONE` |

### State diagram

```
                    ┌─────────────────────────────────────────────────────┐
                    │  SYSTEM ENTRY                                       │
                    │  has_pending_vision=True → planner (creates task)   │
                    └─────────────────────────────────────────────────────┘
                                          │
                                          ▼
                    ┌─────────────────────────────────────────────────────┐
            ┌──────►│  TASK OPEN                                          │
            │       │  branch_exists=False → coder                       │
            │       └─────────────────────────────────────────────────────┘
            │                             │ coder: creates branch, commits
            │                             ▼
            │       ┌─────────────────────────────────────────────────────┐
            │       │  CODING                                              │
            │       │  branch_exists=True, commits_ahead=True             │
            │       │  last_commit=CODER_WORK → qa                        │
            │       └─────────────────────────────────────────────────────┘
            │                             │ qa: reviews
            │                  ┌──────────┴──────────┐
            │                  ▼                     ▼
            │   last_commit=QA_OTHER          last_commit=QA_PASSED
            │        → coder                    → ACTION_QA_PASSED
            └──────────┘                              │
                                                      │ orchestrator: merge + close board
                                                      ▼
                    ┌─────────────────────────────────────────────────────┐
                    │  COMPLETE (task removed from board)                  │
                    └─────────────────────────────────────────────────────┘
```

Additional edge: if a task's branch exists, has no commits ahead, but IS
already merged into dev (stale board entry), the orchestrator emits
`ACTION_CLOSE_BOARD` to clean up without spawning an agent.

### Routing pseudocode (`route()` / `_derive_task_state()`)

```python
def route(state):
    # 1. Block overrides everything
    if state.block == HARD:
        return None           # hard-blocked, wait for human

    if state.block == SOFT:
        return "planner"      # planner resolves ambiguity

    # 2. No open task
    if not state.has_open_task:
        if state.has_pending_vision:
            return "planner"  # planner turns vision into tasks
        return None           # COMPLETE

    # 3. Branch absent — task exists but work hasn't started
    if not state.branch_exists:
        return "coder"        # coder creates branch and first commit

    # 4. Branch exists but no new commits
    if not state.commits_ahead:
        if state.merged_into_dev:
            return ACTION_CLOSE_BOARD  # stale board entry after merge
        return "coder"        # branch just created, coder should add work

    # 5. Branch has work — inspect last commit
    if state.last_commit == QA_PASSED:
        return ACTION_QA_PASSED  # orchestrator merges and closes

    if state.last_commit == QA_OTHER:
        return "coder"        # QA found issues, coder fixes

    return "qa"               # default: coder committed, QA reviews
```

### Important invariant: branch deleted AFTER board closed

The merge sequence in `_merge_feature_into_dev` is:

1. `git merge --no-ff <feature>`
2. Close task on board (filesystem cache write — no git commit)
3. `git worktree remove`
4. `git branch -D <feature>`

The branch is deleted **last**.  Therefore: if a task is still `open` on the
board, `branch_exists=False` means the branch was **never created**, not that
it was merged and cleaned up.  Checking `merged_into_dev` for a non-existent
branch would be unreliable (git exits 128 for unknown refs) and wrong.

---

## Block Detection

`_has_unresolved_block(messages)` scans Telegram messages **newest to
oldest**.  It skips:

- Boot messages (`[orc](boot)`)
- Messages from humans (non-`[role](state)` format)
- Messages from unknown agents

The first matching agent message wins:

| Message pattern | Result |
|-----------------|--------|
| `[*](blocked)` | `(agent, HARD)` |
| `[*](soft-blocked)` | `(agent, SOFT)` |
| `[orc](resolved)` | `(None, None)` — block cleared |
| Any other terminal state (`done`, `ready`, …) | `(None, None)` — block cleared |

**Note**: if two agents are blocked simultaneously, only the newest block is
detected.  The dispatcher suppresses all dispatch on a hard block, so this edge
case cannot normally occur.  On a soft block the dispatcher spawns a single
planner and halts further dispatch for all tasks (by design — the planner may
need to re-plan across tasks).

---

## System-Level Model

> **Note**: The multi-task system-level model (`SystemState`, `TaskState`, `system_route`,
> `system_successors`) described in earlier revisions of this ADR was removed during
> the refactor that unified the board into a single `tasks:` list.  The dispatcher
> now iterates tasks individually, calling `route()` per task via
> `workflow._derive_task_state()`.  The single-task `WorldState` model and its
> deadlock proofs remain in force.

### State space size

| Dimension | Values |
|-----------|--------|
| Per-task git states | ~12 valid combinations |
| Concurrent tasks | up to 4 (typical squad) |
| distinct task combinations (frozenset) | ≤ 12 |
| pending_visions | 0 / 1 / 2 |
| block | NONE / SOFT / HARD |

Total system states explored by BFS: **well under 1 000**.  BFS completes in
milliseconds.

---

## Deadlock-Freedom Proofs

Both models are tested exhaustively in `tests/test_state_machine.py`.

### Per-task: `TestDeadlockFreedom.test_no_deadlocks`

Algorithm:
1. BFS from all plausible entry `WorldState`s → collect every reachable state.
2. Build the reverse adjacency graph.
3. BFS backward from `COMPLETE` terminal states.
4. Assert every non-hard-blocked state is in the backward-reachable set.

### System-level deadlock proofs

> **Note**: The `test_system_no_deadlocks` tests and `TestSystemCrossChecks`
> were removed along with `SystemState`.  Only the per-task proof remains.

---

## Formal Model vs. Implementation Cross-Checks

`TestRouteMatchesImplementation` in `tests/test_state_machine.py` runs
parametrised tests that:
1. Monkeypatch the git query functions in `orc.engine.workflow` with known return values.
2. Call `workflow._derive_task_state()` (the real implementation).
3. Construct the corresponding `WorldState` and call `route()` (the model).
4. Assert both return the same action.

`TestSentinelAlignment` cross-checks that the sentinel constants in the
dispatcher match the values expected by the formal model.

---

## Known Limitations & Design Choices

| # | Issue | Severity | Status |
|---|-------|----------|--------|
| 1 | `merged_into_dev` not checked for absent branch (would be unreliable) | Low | Fixed — branch-absent path returns `"coder"` unconditionally |
| 2 | Soft-block pauses all task dispatch (not just the blocked task) | Medium | By design — planner may need to re-plan globally |
| 3 | Board read-modify-write uses `filelock.FileLock` (30 s timeout); agent tool scripts acquire the same lock | Low | Fixed — `FileBoardManager` and agent tool scripts share `.board.lock` |
| 4 | Simultaneous blocks: only newest detected | Low | Acceptable — hard-block suppresses all dispatch so co-occurrence is rare |
| 5 | Per-task model only; no system-level (N-task) formal proof | Model limitation | Acceptable — dispatcher runs tasks independently; per-task proof covers each |

---

## Files

| File | Role |
|------|------|
| `src/orc/engine/state_machine.py` | Formal model (`WorldState`, `route`, `LastCommit`, `BlockState`) |
| `src/orc/git.py` | Git operations (`Git` class, low-level subprocess wrapper) |
| `src/orc/engine/workflow.py` | Imperative implementation (`_derive_task_state` — reads board status via `_STATUS_TO_LAST_COMMIT`) |
| `src/orc/engine/dispatcher.py` | Parallel scheduler (`_dispatch`, sentinel handling) |
| `src/orc/coordination/board/_board.py` / `_manager.py` | Board YAML CRUD; `FileBoardManager` with `filelock.FileLock`; `TaskStatus` enum |
| `tests/test_state_machine.py` | Deadlock proofs + cross-checks |
