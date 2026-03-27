# ADR-0005: Workflow Engine (replacing multi-agent dispatch)

**Status:** Accepted  
**Date:** 2026-03

## Context

After five end-to-end test runs, ORC's multi-agent architecture exhibited
recurring failure modes that were structural, not incidental:

- **Planner storms**: The planner agent created duplicate tasks in an
  infinite loop because `create_task` had no deduplication and the vision
  filter was broken.
- **Merger noop**: The merger agent spent 5+ iterations reading instruction
  files from disk, then ran a single git command.  Context got buried.
- **QA permission errors**: Compound shell commands failed permission checks.
- **Noop detection fragility**: Board-snapshot diffing couldn't reliably
  detect non-progress across all agent types.
- **Token waste**: Every agent's first 3-5 iterations were spent reading
  instruction files — work that should be part of the prompt.

These share a common root: **the architecture pushed too much responsibility
into LLM agents** (reading instructions, navigating git, calling the right
tool with the right arguments) instead of having the orchestrator drive
deterministic workflows with LLM calls at decision points only.

## Decision

Replace the multi-agent system with a **workflow engine** where:

- The **orchestrator** drives transitions between stages
- **LLM calls** are workers at decision points (not autonomous agents)
- Only the **coder** gets a full agentic loop (it needs creative autonomy)
- Everything else is **deterministic + single LLM call**

### Before

```
Dispatcher → spawn(planner) → [50 iterations] → spawn(coder) →
           → spawn(qa) → [20 iterations] → spawn(merger) → [10 iterations]
```

### After

```
Dispatcher → plan(vision) → [1 LLM call] → spawn(coder) →
           → review(task) → [test + 1 LLM call] → merge(task) → [git]
```

### Operations replacing agents

| Stage | Before | After |
|-------|--------|-------|
| Plan | Full agentic loop (50+ iters) | Single structured LLM call returning JSON |
| Code | Reads instructions from disk | Instructions inlined in system prompt |
| Review | Full agentic loop (20+ iters) | `just test` + single LLM call with diff |
| Merge | Full agentic loop (10+ iters) | `git merge --no-ff` + bounded LLM for conflicts |

### Implementation

Three new modules in `src/orc/engine/operations/`:

- **`plan.py`**: `plan_vision()` reads a vision document, calls the LLM with
  `response_format: json_object`, and returns a list of `TaskSpec` objects.
  The orchestrator creates tasks directly on the board.

- **`review.py`**: `review_task()` runs the test suite in the feature
  worktree, computes `git diff dev...feature`, and makes a single LLM call
  with the diff + test results.  Returns a structured `ReviewResult`.

- **`merge.py`**: `merge_task()` attempts `git merge --no-ff`.  If it
  succeeds, done.  If there are conflicts, it spawns a bounded `AgentRunner`
  (max 20 iterations) with file and shell tools for creative conflict
  resolution.

The `AgentRole` enum retains all four values (PLANNER, CODER, QA, MERGER)
for backward compatibility with squad configs, state machine routing, and
TUI display.  Only CODER produces a spawned agent process.

## Consequences

### Positive

- **Eliminates infinite-loop risk** for planner, QA, and merger
- **Eliminates instruction-reading waste** (~5 iterations × 3 roles saved)
- **Deterministic test/merge stages** have predictable execution time
- **Noop detection is trivial** — operations return structured results
- **Simpler error handling** — operation failures are synchronous exceptions
- **Fewer processes** — only coder agents spawn threads/processes

### Negative

- **Less flexibility** for planner/QA/merger — they can't adapt on the fly
  like a full agent could (acceptable trade-off: these stages are formulaic)
- **Merge conflict resolution** still needs a bounded agentic loop (this is
  genuine creative work that can't be reduced to a single call)

### Supersedes

- ADR-0003 (MCP Server): The MCP server subprocess is no longer used.
  Tools are called in-process.  The permission model still applies to coders.
