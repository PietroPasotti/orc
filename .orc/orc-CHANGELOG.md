## 0001-status-view (closed 2026-03-11T12:40:50Z)

**Vision summary:** A real-time TUI status panel for `orc run` that displays per-agent progress (name, model, status, current task/vision doc, runtime, worktree) broken down by role (planner, coder, qa). The panel also shows global metadata: whether the dev branch is ahead of main, Telegram connectivity, the AI backend in use, and the current loop count vs. the configured maximum.

**Implemented by:**
- `orc/work/0001-live-tui-status-view.md`
