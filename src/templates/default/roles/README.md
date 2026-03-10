# Role overrides

Drop `.md` files here to override the bundled agent role prompts for this
project.  Any file placed here takes precedence over the package defaults.

Expected filenames:

- `planner.md` – instructions for the planner agent
- `coder.md`   – instructions for the coder agent
- `qa.md`      – instructions for the QA agent

If a file is absent the bundled template is used unchanged.

To select the AI model for each role, set it in the squad profile
(``orc/squads/*.yaml``) rather than in the role file.
