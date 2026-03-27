# Agent role overrides

Place role definitions here to override the bundled coder agent prompt for
this project.  Any definition found here takes precedence over the package
defaults.

## Structure

Only the **coder** role is spawned as an agent with a full agentic loop.
Planning, review, and merge are handled as orchestrator operations (single
LLM calls, not multi-turn agent sessions).

### Directory format (recommended)

Create a sub-directory named `coder/`.  Place a `_main.md` file inside it as
the entry point, then add as many additional `.md` files as you like for each
logical sub-topic.  The loader assembles them in this order:

1. `_main.md` (always first)
2. All remaining `*.md` files, alphabetically

YAML frontmatter (between `---` markers) is stripped from every file before
assembly.

```
agents/
├── _shared/
│   └── _main.md          ← shared instructions for all agents
└── coder/
    ├── _main.md          ← identity + "before you start"
    ├── constraints.md
    ├── exit-states.md
    ├── git-workflow.md
    ├── permissions.md
    └── responsibilities.md
```

### Single-file format (legacy)

Drop a single `coder.md` file.  The directory format takes precedence when
both exist.

---

If no override is present, the bundled default is used unchanged.

To select the AI model, set it in the squad profile (`.orc/squads/*.yaml`)
rather than in the role file.
