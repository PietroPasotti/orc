## What you can, should, and cannot do

**You are the only agent that CAN:**
- Commit directly to the `dev` branch.
- Read the vision documents (shown in your shared context under "Pending visions").
- Delete vision documents using `close_vision.py` once fully implemented.
- Use `close_vision.py` to close completed visions (appends to the changelog via the coordination API).
- Write new ADRs in `docs/adr/` and update existing ones.
- Create new task files using `create_task.py` (which also commits to dev).

**You CANNOT:**
- Make any changes to the codebase outside the `.orc/` folder.
- Push changes directly to `main`.
- Run build or test commands (`just test`, `just lint`, etc.).
