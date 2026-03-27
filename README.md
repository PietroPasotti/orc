<div align="center">

<img src="assets/icon.png" alt="orc" width="180"/>

# orc

**An AI workflow engine that turns product visions into code.**

[![CI](https://github.com/PietroPasotti/orc/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/PietroPasotti/orc/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/qorc?color=blue)](https://pypi.org/project/qorc/)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)](https://github.com/PietroPasotti/orc)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/PietroPasotti/orc/blob/main/LICENSE)

</div>

---

Write a vision document. Run `orc run`. Walk away.

orc reads your high-level feature descriptions, breaks them into tasks, spawns AI coder agents, reviews the results, and merges passing work — all on a separate `dev` branch so you can keep working on `main`.

<div align="center">

![How orc works](assets/orc.drawio.svg)

</div>

## ✨ Highlights

- **Board-driven workflow** — a YAML kanban board is the single source of truth; the orchestrator drives a plan → code → review → merge pipeline.
- **Parallel coders** — scale from 1 coder to many, each in its own git worktree, with configurable tool permissions.
- **Git-native** — every decision is a commit. The full audit trail lives in your repo.
- **Sandboxed by default** — agents run in `confined` mode with explicit tool allow-lists. Opt in to `yolo` when you trust the environment.
- **Backend-agnostic** — uses OpenAI-compatible LLM APIs (Gemini, OpenAI, GitHub Models).
- **Telegram integration** — optional real-time notifications and human-in-the-loop unblocking.

---

## 📦 Installation

```bash
pip install qorc

# or, with uv:
uv add qorc
```

> Requires **Python 3.13+**.

---

## 🚀 Quick start

```bash
cd your-project/
orc bootstrap          # scaffold .orc/ config directory
$EDITOR .env           # fill in credentials (see below)
orc run                # let the orchestra play
```

That's it. orc will plan tasks, write code, review changes, and merge passing work into your `dev` branch.

### Key commands

| Command | What it does |
|---|---|
| `orc bootstrap` | Scaffold the `.orc/` directory with roles, squads, and vision templates |
| `orc run` | Run the dispatch loop (plan → code → review → merge) |
| `orc run --maxloops 0` | Loop until all visions are implemented |
| `orc run --squad broad` | Use a custom squad profile |
| `orc status` | Print current board state |
| `orc merge` | Rebase `dev` on `main` and fast-forward merge |

---

## 🏗️ How it works

orc is a **workflow engine**.  On every cycle it reads the **board** (`.orc/work/board.yaml`) and drives a pipeline:

```
pending visions          → plan   (single LLM call → creates tasks)
open task, no branch     → coder  (full agentic loop in git worktree)
coder done               → review (run tests + single LLM review)
review approved          → merge  (git merge, LLM for conflicts)
review rejected          → coder  (retry with feedback)
```

Only the **coder** is a full agent with creative autonomy.  Planning, review, and merge are **orchestrator operations** — deterministic steps with targeted LLM calls.  This eliminates the infinite-loop and instruction-reading failure modes of a multi-agent system.

Board tools are called in-process by the `ToolExecutor`.  Each coder agent works in its own git worktree with role-appropriate tool permissions.

Work happens on a `dev` branch. You keep working on `main`. When you're ready, `orc merge` brings everything together (with automatic conflict resolution by a coder agent if needed).

> **Tip:** configure the integration branch name via `orc-dev-branch` in `.orc/config.yaml`.
> **Tip:** namespace all orc branches with `orc-branch-prefix` (e.g. `orc` → `.orc/feat/0001-foo`).

---

## 🗂️ Project layout after bootstrap

```
your-project/
  .orc/
    agents/                 ← coder instructions (override bundled defaults)
    squads/
      default.yaml          ← squad profile (coder count, model, permissions)
    config.yaml             ← project settings
    justfile                ← convenience recipes
    vision/                 ← your feature descriptions go here
    work/
      board.yaml            ← kanban board (managed by orc)
  .env.example              ← credential template
```

Existing files are **never overwritten** unless `--force` is passed.

---

## ⚙️ Configuration

### Credentials (`.env`)

Copy `.env.example` → `.env` and fill in:

```bash
GEMINI_API_TOKEN=...           # Gemini API key (default provider)
# OPENAI_API_KEY=...           # OpenAI API key (optional)
# GH_TOKEN=...                 # GitHub Models token (optional)
```

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_TOKEN` | — | Gemini API key. Required when using the default `gemini` provider. |
| `OPENAI_API_KEY` | — | OpenAI API key. Required when using the `openai` provider. |
| `GH_TOKEN` | — | GitHub token. Required when using the `github` provider. |
| `COLONY_TELEGRAM_TOKEN` | — | Telegram bot token for notifications. |
| `COLONY_TELEGRAM_CHAT_ID` | — | Telegram chat ID. Required when token is set. |
| `ORC_DIR` | `.orc/` | Override config directory path. |
| `ORC_LOG_LEVEL` | `INFO` | `DEBUG` · `INFO` · `WARNING` · `ERROR` |
| `ORC_LOG_FORMAT` | `console` | `console` or `json` |
| `ORC_LOG_FILE` | `.orc/logs/orc.log` | Log file path. Empty string disables file logging. |

### Config file (`.orc/config.yaml`)

| Key | Default | Description |
|---|---|---|
| `orc-dev-branch` | `dev` | Integration branch name. |
| `orc-branch-prefix` | _(empty)_ | Optional prefix for orc-owned branches. |
| `orc-worktree-base` | `.orc/worktrees` | Base directory for git worktrees. |

---

## 🎻 Squad profiles

Squads define the composition and permissions of your agent team. They live in `.orc/squads/{name}.yaml`.

```yaml
name: broad
description: High-throughput squad for large projects.

permissions:
  mode: confined             # "confined" (default) or "yolo"
  allow_tools:
    - "shell(just:*)"
  deny_tools:
    - "shell(git push:*)"

composition:
  - role: planner
    count: 1
    model: claude-sonnet-4.6
  - role: coder
    count: 4
    model: claude-sonnet-4.6
    permissions:
      allow_tools:
        - "shell(npm:*)"
        - "shell(cargo:*)"
  - role: qa
    count: 2
    model: claude-sonnet-4.6
    review-threshold: HIGH

timeout_minutes: 180
```

### Permission modes

| Mode | Behaviour |
|---|---|
| `confined` | Agents may only use board tools, `read`, `write`, `shell(git:*)`, plus explicit `allow_tools`. Default. |
| `yolo` | Unrestricted tool access. Use for trusted environments or debugging. |

### QA review threshold

Controls which severity of issues causes QA to reject work back to coders:

| Threshold | Rejects on |
|---|---|
| `CRITICAL` | Critical failures only (most lenient) |
| `HIGH` | High-severity and above |
| `MID` | Medium-severity and above |
| `LOW` | Any issue (strictest, **default**) |

> The planner count must always be `1`. Scale throughput by adding coders and QA reviewers.

---

## 📱 Telegram notifications

Optionally monitor your agents in real time via Telegram. Set up a bot through [@BotFather](https://t.me/BotFather), add it to a group, and configure `COLONY_TELEGRAM_TOKEN` + `COLONY_TELEGRAM_CHAT_ID`.

Agents post structured status updates:

```
[coder-1](done) 2026-03-01T12:45:00Z: Implemented task 0002.
[qa-1](passed) 2026-03-01T13:00:00Z: No issues found.
[coder-2](blocked) 2026-03-01T14:00:00Z: Need clarification on auth flow…
```

---

## 🤝 Contributing

See **[CONTRIBUTING.md](CONTRIBUTING.md)** for the development workflow, commit conventions, and test-first policy.

```bash
git clone https://github.com/PietroPasotti/orc.git && cd orc
just install   # deps + git hooks
just test      # run the suite
```

---

## 📜 License

[Apache 2.0](https://github.com/PietroPasotti/orc/blob/main/LICENSE)
