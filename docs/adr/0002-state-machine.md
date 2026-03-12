# ADR-0002 — Git-Backed State Machine

**Status**: Accepted

---

## Context

`orc` is a multi-agent orchestrator.  It decides which agent to invoke next
(planner, coder, or qa) by reading git state and Telegram message history —
no database, no in-process state.  This document describes the state machine
formally, explains the design choices, and records where the formal model lives
and how its soundness is verified.

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
       └─ git._derive_state_from_git()
            ├─ board._active_task_name()             # board YAML
            └─ git._derive_task_state(task)          # git queries
```

A **formal model** (`state_machine.py`) mirrors this logic in pure Python and
is tested exhaustively for deadlock-freedom via BFS.

---

## Per-Task State Machine

### Inputs

| Input | Source | Notes |
|-------|--------|-------|
| `has_open_task` | board YAML `open:[]` | first open entry |
| `has_pending_vision` | board YAML `visions:[]` | any un-planned vision |
| `branch_exists` | `git branch --list <feat/NNNN-*>` | feature branch present |
| `commits_ahead` | `git rev-list <branch> ^main` | any commits not in main |
| `merged_into_dev` | `git merge-base --is-ancestor <branch> dev` | only checked when branch exists and has no commits ahead |
| `last_commit` | message of HEAD on feature branch | `qa(passed):`, `qa(*):`, or other |
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
2. Close task on board + commit board update
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

The system model extends the per-task model to N concurrent tasks.

### SystemState

```python
@dataclass(frozen=True)
class SystemState:
    tasks: frozenset[TaskState]   # open tasks (structurally distinct)
    pending_visions: int          # capped at 2 to keep state space finite
    block: BlockState             # system-wide block
```

`TaskState` captures the per-task git fields (`branch_exists`, `commits_ahead`,
`merged_into_dev`, `last_commit`).  Because the model uses a `frozenset`, two
tasks with identical git state are treated as one (structural equivalence).
This keeps the state space finite without losing soundness properties.

### Interleaving semantics

`system_successors()` applies **one agent's outcome at a time**.  This avoids
the Cartesian product explosion (which would be O(outcomes^N)) while still
exploring every feasible execution order.

```
system_route(s) → { task₁: "coder", task₂: "qa", … }   # all eligible

system_successors(s) → one new state per (task, outcome) pair
                        (not one state per all-task combination)
```

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

### System-level: `test_system_no_deadlocks[N-tasks]`

Same algorithm over `SystemState`, parametrised by representative starting
configurations with 1–4 tasks in mixed git states.

**Proven**: No non-hard-blocked system state is a deadlock.  Every reachable
state can eventually reach `SystemState(tasks=∅, pending_visions=0)`.

---

## Formal Model vs. Implementation Cross-Checks

`TestRouteMatchesImplementation` in `tests/test_state_machine.py` runs
parametrised tests that:
1. Monkeypatch the git query functions in `orc.git` with known return values.
2. Call `git._derive_task_state()` (the real implementation).
3. Construct the corresponding `WorldState` and call `route()` (the model).
4. Assert both return the same action.

`TestSystemCrossChecks` does the same for `system_route()` vs. `route()`.

---

## Known Limitations & Design Choices

| # | Issue | Severity | Status |
|---|-------|----------|--------|
| 1 | `merged_into_dev` not checked for absent branch (would be unreliable) | Low | Fixed — branch-absent path returns `"coder"` unconditionally |
| 2 | Soft-block pauses all task dispatch (not just the blocked task) | Medium | By design — planner may need to re-plan globally |
| 3 | Board read-modify-write not locked | Low | Acceptable — dispatcher is single-threaded; board writes are serialized |
| 4 | Simultaneous blocks: only newest detected | Low | Acceptable — hard-block suppresses all dispatch so co-occurrence is rare |
| 5 | `frozenset[TaskState]` deduplicates structurally identical tasks | Model limitation | Acceptable — structural equivalence is sufficient for liveness proofs |

---

## Files

| File | Role |
|------|------|
| `src/orc/state_machine.py` | Formal model (`TaskState`, `WorldState`, `route`, `successors`, `SystemState`, `system_route`, `system_successors`) + coarse enum (`WorkflowState`, `WorkflowStateMachine`) |
| `src/orc/git.py` | Imperative implementation (`_derive_task_state`, `_derive_state_from_git`) |
| `src/orc/workflow.py` | Top-level routing (`determine_next_agent`, `_has_unresolved_block`) |
| `src/orc/dispatcher.py` | Parallel scheduler (`_dispatch`, sentinel handling) |
| `src/orc/board.py` | Board YAML CRUD (`_active_task_name`) |
| `tests/test_state_machine.py` | Deadlock proofs + cross-checks |
