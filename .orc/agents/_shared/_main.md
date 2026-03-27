---
symbol: "🤝"
---
# Shared agent instructions

These instructions apply to **all** agent roles.
Role-specific instructions in your role's `_main.md` may extend or override
what is written here.

---

## Tool-call discipline

> **Never emit a bare text response while work is still pending.**

Every response you produce must include at least one tool call until **all**
your assigned work is complete or you have explicitly signalled your exit state.
If the AI runtime receives a text-only response (no tool calls) it treats it
as your **final answer** and terminates the session — even if you intended to
keep working.

### Waiting for background agents

When you launch background sub-agents (explore, task, etc.) and need their
results:

* Always call `read_agent` with **`wait: true`** and a generous `timeout`
  (120–300 seconds).
* If the agent is still running after the timeout, call `read_agent` **again**
  — do not emit text like "I'm waiting" or "checking back later."
* Never narrate your intent to wait. **Act** by calling the tool.

### General rule

Think of it this way: if you have more work to do, your response **must**
contain a tool call. A response with only text signals "I'm done" to the
runtime.

## Exit behaviour

When you truly cannot proceed — tools failed, context is missing, you hit an
unresolvable blocker — signal your exit state explicitly:

* Use the Telegram messaging tool to post your state (`ready`, `blocked`,
  `done`, or the role-equivalent).
* Never exit silently without having done work **or** signalled a state.

The orchestrator monitors board state before and after your run. If you exit
without changing anything, it will treat the run as a **noop failure** and
abort. Make sure every run either produces observable output or signals why
it could not.

## Shell-command hygiene

Prefer short-lived, synchronous shell commands. Avoid long-running
asynchronous shell sessions — tool session IDs can become invalid between
turns, leaving you unable to retrieve output. If a command might take more
than a few seconds, run it synchronously and let the tool handle the wait.
