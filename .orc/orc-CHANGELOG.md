## 0001-nicer-tui (closed 2026-03-11T16:51:25Z)

**Vision summary:** A refined three-column TUI layout for `orc run` that organises agents by role into dedicated columns (Planner | Coder | QA), each labelled with the role name and shared model. Individual agent cards show status, current task, worktree, and elapsed time. A global header row displays loop counter, dev-ahead count, backend, and Telegram connectivity.

**Implemented by:**
- `.orc/work/0001-live-tui-status-view.md`
- `.orc/work/0003-nicer-tui-three-column-layout.md`

## 0001-status-view (closed 2026-03-11T12:40:50Z)

**Vision summary:** A real-time TUI status panel for `orc run` that displays per-agent progress (name, model, status, current task/vision doc, runtime, worktree) broken down by role (planner, coder, qa). The panel also shows global metadata: whether the dev branch is ahead of main, Telegram connectivity, the AI backend in use, and the current loop count vs. the configured maximum.

**Implemented by:**
- `.orc/work/0001-live-tui-status-view.md`

## 0005-orc-merge-auto-worktree-fix (closed 2026-03-13T17:34:37Z)

**Vision summary:** Fixed `orc merge --auto` to work correctly when the `dev` branch is checked out as a worktree. The fast-forward merge now runs via `git -C <repo_root> merge --ff-only dev` against the main worktree instead of attempting a checkout inside the dev worktree. An `UntrackedFilesWouldBeOverwrittenError` exception is raised and surfaced to the user with a clear message if untracked files would block the merge.

**Implemented by:**
- `.orc/work/0005-orc-merge-auto-worktree-fix.md`

## 0006-board-yaml-in-main-worktree (closed 2026-03-13T17:34:37Z)

**Vision summary:** Prevented the stale `board.yaml` conflict that caused `orc merge --ff-only` to abort with an "untracked file would be overwritten" error. The `orc bootstrap` command now commits an initial empty `board.yaml` to the `main` branch so git tracks it and can overwrite it cleanly on merge.

**Implemented by:**
- `.orc/work/0006-board-yaml-main-worktree.md`

## 0004-orc-status-plain-output (closed 2026-03-13T17:21:56Z)

**Vision summary:** Added a `--plain` flag to `orc status` that forces plain-text output even when running in an interactive terminal. Without `--plain`, the existing TTY → TUI / non-TTY → plain behaviour is unchanged. The flag makes `orc status` usable in CI, scripts, pipes, and AI-agent subprocesses where a full Textual TUI is unavailable or unwanted.

**Implemented by:**
- `.orc/work/0004-orc-status-plain-flag.md`

## 0004-orc-status-plain-output (closed 2026-03-14T15:18:26Z)

**Summary:** Added --plain flag to 'orc status' that prints current workflow state as plain text and exits without launching the TUI. Works in CI, scripts, pipes, and AI agent subprocesses. The plain output covers board state, agent statuses, and git branch summary.

**Implemented by:** 0010-code-cleanup.md

## 0005-orc-merge-auto-worktree-fix (closed 2026-03-14T15:18:33Z)

**Summary:** Fixed 'orc merge --auto' to perform the fast-forward merge from the main worktree (cwd=repo_root) instead of inside the dev worktree. Added UntrackedMergeBlockError with clear per-file error messages when untracked files block the merge.

**Implemented by:** 0010-code-cleanup.md
