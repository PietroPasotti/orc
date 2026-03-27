# Operations Reference

ORC uses a **workflow engine** model where only the **coder** is a full
agentic loop.  All other workflow stages (planning, review, merge) are
**orchestrator operations** — deterministic steps with single LLM calls at
decision points.

## Pipeline

```
Vision arrives
    │
    ▼
┌──────────────────┐
│  PLAN (operation) │  Single structured LLM call.
│                   │  Orchestrator reads vision, LLM returns JSON task specs.
└────────┬─────────┘
         ▼
┌──────────────────┐
│  CODE (agent)     │  Full agentic loop — the only real agent.
│                   │  System prompt includes all instructions inline.
└────────┬─────────┘
         ▼
┌──────────────────┐
│  REVIEW (operation)│  Orchestrator runs `just test`, computes diff,
│                   │  single LLM call returns pass/fail verdict.
└────────┬─────────┘
         ▼
┌──────────────────┐
│  MERGE (operation) │  Deterministic `git merge --no-ff`.
│                   │  On conflict: bounded LLM loop (max 20 iters).
└───────────────────┘
```

## Module layout

| Module | Purpose |
|--------|---------|
| `src/orc/engine/operations/plan.py` | `plan_vision()` — structured LLM call, returns task specs |
| `src/orc/engine/operations/review.py` | `review_task()` — test run + LLM review verdict |
| `src/orc/engine/operations/merge.py` | `merge_task()` — git merge + LLM conflict resolution |
| `src/orc/engine/dispatcher.py` | Orchestrator loop calling operations + spawning coders |
| `src/orc/ai/tools.py` | `ToolExecutor` — built-in file/shell/board tools |
| `src/orc/mcp/tools.py` | Board tool functions (get_task, create_task, etc.) |
| `src/orc/mcp/client.py` | HTTP client for coordination API (Unix socket) |

## Board tools

Board operations are called **in-process** by the `ToolExecutor` (no separate
MCP server).  The coder agent gets access to:

| Tool | Description |
|------|-------------|
| `get_task` | Read task content and conversation |
| `update_task_status` | Change task status (planned → in-progress → done) |
| `add_comment` | Add a comment to a task |
| `close_task` | Signal coder work is complete |

The orchestrator operations call board functions directly (not through
`ToolExecutor`), using the `BoardService` protocol defined in
`src/orc/engine/services.py`.

## Permission model

Tool permissions are configured per-squad in `.orc/squads/*.yaml`.  Only
coder agents use permissions (operations run as the orchestrator).  See
ADR-0003 for the full permission resolution model.
