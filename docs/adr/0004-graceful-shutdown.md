# ADR-0004 — Graceful Shutdown ("exit-soon")

**Status**: Accepted
**Date**: 2026-03-16

---

## Context

`orc run` currently handles `SIGINT`/`SIGTERM` by immediately killing all
running agents, unassigning their board tasks, and exiting with code 130.
While functional, this is abrupt: agents mid-way through writing code or
running tests lose all in-flight work, and merge operations may be
interrupted at unsafe points.

Vision `0002-exit-soon` asks for a "terminate ASAP" mechanism that lets the
user signal the system to stop as soon as possible **without** discarding
recoverable work.

### Open questions from the vision (resolved below)

| Question | Resolution |
|----------|------------|
| How much work can we recover if we kill a coder mid-flight? | Feature-branch commits survive; the task is unassigned so the next run re-dispatches a coder that can inspect existing branch state and continue. |
| What about mid-merge or mid-rebase? | Stage 1 waits for in-progress merges to finish. Stage 2 forcibly interrupts, but the existing crash-recovery logic in `_drain_merge_queue` handles stale state on the next run. |
| Should orc be a detached process? | Out of scope — daemon mode is a separate concern (PID files, log rotation, etc.) and not required for graceful shutdown. |

---

## Decision

### Two-stage shutdown

| Stage | Trigger | Behaviour |
|-------|---------|-----------|
| **Drain** (stage 1) | First `SIGINT`/`SIGTERM`, or TUI `q` key | Transition `Dispatcher.phase` to `DispatcherPhase.DRAINING`. **Stop dispatching new agents.** Let every running agent finish naturally. Let any in-progress merge complete. |
| **Kill** (stage 2) | Second signal while draining, **or** a configurable timeout (default: the squad `timeout_minutes`) | Kill all remaining agents immediately, unassign their tasks, exit. |

### Implementation sketch

1. **`Dispatcher.phase: DispatcherPhase`** — an enum (`RUNNING` / `DRAINING`)
   checked at the top of `_dispatch_agents()`; when `DRAINING`, skip all
   dispatch and return `0`. `RunState.draining` is a derived property that
   reads from `RunState.dispatcher_phase`.

2. **`_shutdown_handler`** — on first call, set the flag and log
   `"drain requested — waiting for running agents"`. On second call (or if
   already draining), raise `_ShutdownSignal` as today for the hard kill path.

3. **`_loop()` termination** — the existing `pool.is_empty()` check already
   causes the loop to exit when all agents are done; no new exit logic is
   needed for stage 1. The loop naturally terminates once the last agent
   completes and no new agents are dispatched.

4. **TUI feedback** — `RunState.draining` is a derived `@property` that returns
   `True` when `dispatcher_phase is DispatcherPhase.DRAINING`; the header shows
   `"⏳ draining…"` so the user knows the system is winding down.

5. **`run_tui`** — the TUI `q` binding calls a callback that transitions
   `Dispatcher.phase` to `DRAINING` instead of exiting the app
   immediately.

### Recovery semantics

No change from today for killed agents: tasks are unassigned, feature
branches retain their commits, and the next `orc run` picks up where things
left off. The improvement is that stage 1 avoids killing agents at all —
they finish their session and the orchestrator exits cleanly.

### Exit codes

| Scenario | Exit code |
|----------|-----------|
| Clean drain (all agents finished) | `0` |
| Forced kill (stage 2) | `130` (unchanged) |

---

## Out of scope

- **`orc stop` CLI command** — a future enhancement; the signal-based
  approach is sufficient for now.
- **Daemon / detached mode** — separate vision, separate ADR.

---

## Consequences

- `_ShutdownSignal` is no longer raised on the first signal; the handler
  becomes stateful (first call → flag, second call → exception).
- `_dispatch_agents()` gains an early-return guard.
- The TUI `q` binding changes from "exit app" to "trigger drain, then
  exit when pool is empty."
- New tests: drain-mode dispatch suppression, two-stage signal escalation,
  TUI drain indicator.
- `squad.timeout_minutes` becomes the default stage-2 timeout after drain
  is requested.
