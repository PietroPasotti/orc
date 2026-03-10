# Role overrides

Drop `.md` files here to override the bundled agent role prompts for this
project.  Any file placed here takes precedence over the package defaults.

Expected filenames:

- `planner.md` – instructions for the planner agent
- `coder.md`   – instructions for the coder agent
- `qa.md`      – instructions for the QA agent

Each file may start with a YAML front-matter block to select the model:

```
---
model: claude-opus-4-5
---

You are the planner agent for <project>. ...
```

If a file is absent the bundled template is used unchanged.
