# orc

<p align="center">
  <img src="assets/icon.png" alt="orc icon" width="200"/>
</p>

**orc** is a standalone multi-agent orchestrator that turns high-level user-provided product visions into implementation code.

## How it works

```
orc run
  └── planner                   – reads vision docs and #TODOs|#FIXMEs, creates tasks in orc/work/
        └── coder               – implements each task on a feature branch
              └── qa            – reviews the branch, commits chore(<qa-id>.approve.<task>): or chore(<qa-id>.reject.<task>):
                    └── orc     – merges the feature branch into dev, loops back to planner
```

Inter-agent synchronization happens over git and interaction with the user is mediated by a telegram bot. 
The orchestrator inspects the git tree status to determine the current state and decide which agent(s) to run next depending on the pool of available agents.

Orc's work happens on a `dev` branch, so that you can keep working on `main` independently.
Any time control goes to the orchestrator, the orchestrator will rebase `dev` on `main`.
Whenever you're ready to merge `dev` into `main`, run `orc merge` to delegate fixing any conflicts to an agent.

If the dev worktree is dirty when a feature branch is being merged (e.g. from a previously interrupted run), orc automatically resets it to `HEAD` before retrying.  If the merge itself produces conflicts, a coder agent is spawned to resolve them before the run continues.

> **Tip:** the integration branch name (`dev`) is configurable via `orc-dev-branch` in `.orc/config.yaml`.
> **Tip:** all orc-owned feature branches can be namespaced with a prefix via `orc-branch-prefix` in `.orc/config.yaml` (e.g. `orc-branch-prefix: orc` produces branches like `orc/feat/0001-foo`).

## Installation

```bash
pip install qorc
# or with uv:
uv add qorc
```

## Quick start

```bash
# 1. Scaffold the orc/ config directory in your project
cd your-project/
uv run orc bootstrap

# 2. Edit orc/roles/*.md to customise agent instructions (optional)
# 3. Add vision documents to orc/vision/
# 4. Copy .env.example → .env and fill in credentials
# 5. Add to your root justfile (optional):
#       mod orc 'orc/justfile'
# 6. Run
just orc run  # or: uv run orc run, if you don't have just
```

## bootstrap

`orc bootstrap` scaffolds the entire `.orc/` configuration directory structure in one command:

```
your-project/
  .orc/
    roles/              ← bundled generic role templates (edit to suit your needs)
      planner.md        
      coder.md
      qa.md
    squads/
      default.yaml      ← 1 planner, 1 coder, 1 QA
    vision/
      0001-vision.md    ← example vision doc (edit / replace with your own)
      README.md         ← explanation of what vision docs are and how to write them
    work/
      board.yaml        ← empty kanban board, you shouldn't have to touch this
    justfile            ← run / status / merge recipes
  .env.example          ← credential template; copy to .env and fill in
```

See `orc bootstrap --help` for more options.

Existing files are **never overwritten** unless `--force` is passed.

After bootstrapping, the only things left to do are:

1. Customise `orc/roles/*.md` for your project's purposes
2. Drop vision documents into `orc/vision/`, describing features you want implemented.
3. Fill in `.env`.

### .env

Copy `.env.example` to `.env` and fill in:

```bash
COLONY_AI_CLI=copilot          # or "claude"
COLONY_TELEGRAM_TOKEN=...
COLONY_TELEGRAM_CHAT_ID=...
GH_TOKEN=...                   # for copilot backend
```

## Running

```bash
# Run one dispatch cycle (default) — may spawn a full squad in parallel
orc run

# Run until there's no more work to do (all visions are implemented in the dev branch and passing QA)
orc run --maxloops 0

# Use a custom squad profile
orc run --squad broad

# Print current workflow state
orc status

# Rebase dev on main and merge
orc merge
```

## Squad profiles

Squad profiles live in `orc/squads/{name}.yaml` (project-level) or are provided by the package (built-in `default`). They define how many agents of each role may run in parallel:

```yaml
# orc/squads/broad.yaml
planner: 1
coder: 4
qa: 2
timeout_minutes: 180
```

The `planner` count must always be `1`. Scale throughput by adding coders and QA reviewers.

Built-in profiles:
- `default` – 1 planner, 1 coder, 1 QA (sequential)

## (optional) Agent monitoring and unblocking over Telegram channel

All agents can send regular updates through a Telegram bot. Set up a bot via `@BotFather`, add it to a group or channel, and fill in `COLONY_TELEGRAM_TOKEN` and `COLONY_TELEGRAM_CHAT_ID`.

Agents post structured messages:
```
[coder-1](done) 2026-03-01T12:45:00Z: Implemented task 0002.
[qa-1](passed) 2026-03-01T13:00:00Z: No issues found.
```

Occasionally the agents may get stuck working on something, and they'll notify you by sending a message like:
```
[coder-2](blocked) 2026-03-01T14:00:00Z: I'm having trouble implementing task 0003 because I cannot inject the sql in the booper...
```

You can reply to that message in the Telegram thread, and the orchestrator will pick up your response and send it back to the agent as additional context.

## Configuration

### Environment variables
You can configure the orchestrator via environment variables.
These are the supported variables, their defaults, and what they do:

| Variable | Default | Required | Description |
|---|---|---|---|
| `COLONY_AI_CLI` | — | ✅ | AI backend to use. `copilot` (GitHub Copilot CLI) or `claude` (Anthropic). |
| `GH_TOKEN` | — | When `COLONY_AI_CLI=copilot` | GitHub personal access token. Can be omitted if already authenticated via `gh auth login` or `copilot /login`. |
| `ANTHROPIC_API_KEY` | — | When `COLONY_AI_CLI=claude` | Anthropic API key. |
| `COLONY_TELEGRAM_TOKEN` | — | Optional | Telegram bot token. When set, enables Telegram notifications and human-in-the-loop replies. |
| `COLONY_TELEGRAM_CHAT_ID` | — | Optional | Telegram chat ID the bot posts to. Required when `COLONY_TELEGRAM_TOKEN` is set. |
| `ORC_DIR` | `.orc/` or `orc/` in CWD | Optional | Override the path to the orc configuration directory. Useful when the config lives outside the project root. |
| `ORC_LOG_LEVEL` | `INFO` | Optional | Minimum log level. Standard values: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `ORC_LOG_FORMAT` | `console` | Optional | Log output format. `console` for human-readable output, `json` for structured logs. |
| `ORC_LOG_FILE` | `.orc/logs/orc.log` | Optional | Path to the orchestrator log file. Set to an empty string to disable file logging. |
| `ORC_LOG_DIR` | — | Optional | Override the log directory. Sets the log file to `$ORC_LOG_DIR/orc.log` when `ORC_LOG_FILE` is not set. |

### Config file

`.orc/config.yaml` contains additional configuration options, their defaults and what they do:

| Key | Default | Description |
|---|---|---|
| `orc-dev-branch` | `dev` | Integration branch name. Feature branches are merged here after QA passes; `orc merge` fast-forwards it into `main`. |
| `orc-branch-prefix` | _(empty)_ | Optional prefix for all orc-owned branches. E.g. `orc` produces `orc/feat/0001-foo` instead of `feat/0001-foo`. |
| `orc-worktree-base` | `.orc/worktrees` | Base directory for git worktrees. Worktrees are placed at `<base>/<task>`, e.g. `.orc/worktrees/0001-foo`. |
