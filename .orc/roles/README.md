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

Feel free to customize the role prompts as much as you like, but keep in mind that the orchestrator relies on certain conventions (e.g. commit message formats, file paths) to detect state and progress. If you change those conventions in the role prompts, make sure to also update the orchestrator's logic accordingly.
