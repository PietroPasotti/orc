<div align="center">

<img src="assets/icon.png" alt="orc" width="180"/>

# orc

**A multi-agent orchestrator that turns product visions into code.**

[![CI](https://github.com/PietroPasotti/orc/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/PietroPasotti/orc/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/qorc?color=blue)](https://pypi.org/project/qorc/)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)](https://github.com/PietroPasotti/orc)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/PietroPasotti/orc/blob/main/LICENSE)

</div>

---

Write a vision document. Run `orc run`. Walk away.

orc reads your high-level feature descriptions, breaks them into tasks, assigns them to AI agents (planner → coder → QA), and merges the results — all on a separate `dev` branch so you can keep working on `main`.

<div align="center">

![How orc works](assets/orc.drawio.svg)

</div>

## ✨ Highlights

- **Board-driven workflow** — a YAML kanban board is the single source of truth; agents read and write it through a per-agent MCP server.
- **Parallel squads** — scale from 1 coder to many, each in its own git worktree, with configurable tool permissions.
- **Git-native** — every decision is a commit. The full audit trail lives in your repo.
- **Sandboxed by default** — agents run in `confined` mode with explicit tool allow-lists. Opt in to `yolo` when you trust the environment.
- **Backend-agnostic** — works with GitHub Copilot CLI or Anthropic Claude.
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

That's it. orc will plan tasks, write code, run QA, and merge passing work into your `dev` branch.

### Key commands

| Command | What it does |
|---|---|
| `orc bootstrap` | Scaffold the `.orc/` directory with roles, squads, and vision templates |
| `orc run` | Run one dispatch cycle (plan → code → QA → merge) |
| `orc run --maxloops 0` | Loop until all visions are implemented |
| `orc run --squad broad` | Use a custom squad profile |
| `orc status` | Print current board state |
| `orc merge` | Rebase `dev` on `main` and fast-forward merge |

---

## 🏗️ How it works

orc is stateless. On every cycle it reads the **board** (`.orc/work/board.yaml`) and applies a simple dispatch table:

```
no open tasks              → planner
open task, no branch       → coder
open task, coder commits   → qa
qa approved                → merge + loop
qa rejected                → coder (retry)
```

All agent ↔ board communication goes through a **per-agent MCP server** that `orc run` starts automatically. Each agent is scoped to its own role — no direct file access, no cross-agent state leaks.

Work happens on a `dev` branch. You keep working on `main`. When you're ready, `orc merge` brings everything together (with automatic conflict resolution by a coder agent if needed).

> **Tip:** configure the integration branch name via `orc-dev-branch` in `.orc/config.yaml`.
> **Tip:** namespace all orc branches with `orc-branch-prefix` (e.g. `orc` → `.orc/feat/0001-foo`).

---

## 🗂️ Project layout after bootstrap

```
your-project/
  .orc/
    roles/                  ← agent role templates (planner, coder, qa)
    squads/
      default.yaml          ← 1 planner · 1 coder · 1 QA
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
COLONY_AI_CLI=copilot          # "copilot" or "claude"
GH_TOKEN=...                   # for copilot backend (or use `gh auth login`)
# ANTHROPIC_API_KEY=...        # for claude backend
```

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `COLONY_AI_CLI` | — | **Required.** AI backend: `copilot` or `claude`. |
| `GH_TOKEN` | — | GitHub token (copilot backend). Can omit if already authed via `gh`. |
| `ANTHROPIC_API_KEY` | — | Anthropic API key (claude backend). |
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
| `confined` | Agents may only use orc MCP tools, `read`, `write`, `shell(git:*)`, plus explicit `allow_tools`. Default. |
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
