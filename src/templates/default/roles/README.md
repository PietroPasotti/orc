# Role overrides

Place role definitions here to override the bundled agent prompts for this
project.  Any definition found here takes precedence over the package defaults.

## Two supported formats

### Directory format (recommended)

Create a sub-directory named after the role.  Place a `_main.md` file inside
it as the entry point, then add as many additional `.md` files as you like for
each logical sub-topic.  The loader assembles them in this order:

1. `_main.md` (always first)
2. All remaining `*.md` files, alphabetically

YAML frontmatter (between `---` markers) is stripped from every file before
assembly.  Put the `symbol:` key in `_main.md`'s frontmatter.

```
roles/
├── coder/
│   ├── _main.md          ← identity + "before you start"
│   ├── constraints.md
│   ├── exit-states.md
│   ├── git-workflow.md
│   ├── permissions.md
│   └── responsibilities.md
├── planner/
│   └── ...
└── qa/
    └── ...
```

To exclude a module for your project, simply delete or omit that file.

### Single-file format (legacy / simple overrides)

Drop a single `.md` file named after the role:

- `planner.md` – instructions for the planner agent
- `coder.md`   – instructions for the coder agent
- `qa.md`      – instructions for the QA agent

The directory format takes precedence when both exist for the same role.

---

If a role definition is absent, the bundled default is used unchanged.

To select the AI model for each role, set it in the squad profile
(`orc/squads/*.yaml`) rather than in the role file.

Feel free to customize the role prompts, but keep in mind that the orchestrator
relies on certain conventions (e.g. commit message formats, file paths) to
detect state and progress.  If you change those conventions, update the
orchestrator logic accordingly.
