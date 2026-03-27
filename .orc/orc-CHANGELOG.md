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

## 0008-agent-tools-cleanup (closed 2026-03-14T15:40:55Z)

**Summary:** Unified approve_task.py and reject_task.py into a single review_task.py with approved|rejected outcome parameter. Added get_vision.py for planners and get_task.py for coders to fetch file content from the coordination server without direct filesystem access.

**Implemented by:** 0016-agent-tools-cleanup.md

## 0007-orc-status-board-view (closed 2026-03-14T15:47:17Z)

**Summary:** Added Board tab to orc status TUI showing kanban swimlane view (To refine, To do, In progress, Awaiting review, Done) fetched from the coordination API. Falls back gracefully when the server is unreachable.

**Implemented by:** 0014-orc-status-board-view.md

## 0022-planner-tui-status-details (merged 2026-03-15T17:41:16Z)

**Branch:** feat/0022-planner-tui-status-details

**Task:** 0022-planner-tui-status-details.md

**Merge commit:** 8321631

## 0024-remove-git-tree-tab-from-status-tui (merged 2026-03-15T21:28:10Z)

**Branch:** feat/0024-remove-git-tree-tab-from-status-tui

**Task:** 0024-remove-git-tree-tab-from-status-tui.md

**Merge commit:** 76573e4

## tui-squad-and-runtime (merged 2026-03-16T09:44:57Z)

**Branch:** feat/tui-squad-and-runtime

**Task:** tui-squad-and-runtime.md

**Merge commit:** ae9f01d

## 0031-remove-stale-tui-todos (merged 2026-03-16T14:32:12Z)

**Branch:** feat/0031-remove-stale-tui-todos

**Task:** 0031-remove-stale-tui-todos.md

**Merge commit:** 015e93c

## 0036-commit-changelog-after-merge (merged 2026-03-16T16:10:02Z)

**Branch:** feat/0036-commit-changelog-after-merge

**Task:** 0036-commit-changelog-after-merge.md

**Merge commit:** 35d8110

## 0052-implement-llmclient-repr (merged 2026-03-27T18:07:43Z)

**Branch:** feat/0052-implement-llmclient-repr

**Task:** 0052-implement-llmclient-repr.md

**Merge commit:** 839f534

## 0052-implement-llmclient-repr (merged 2026-03-27T18:07:49Z)

**Branch:** feat/0052-implement-llmclient-repr

**Task:** 0052-implement-llmclient-repr.md

**Merge commit:** 839f534

## 0052-implement-llmclient-repr (merged 2026-03-27T18:07:54Z)

**Branch:** feat/0052-implement-llmclient-repr

**Task:** 0052-implement-llmclient-repr.md

**Merge commit:** 839f534

## 0053-add-total-tokens-property-to-chatresponse (merged 2026-03-27T18:14:45Z)

**Branch:** feat/0053-add-total-tokens-property-to-chatresponse

**Task:** 0053-add-total-tokens-property-to-chatresponse.md

**Merge commit:** 19d13f1
