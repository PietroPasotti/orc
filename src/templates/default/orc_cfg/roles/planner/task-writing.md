## Write tasks that stand alone

A task file is both a task list and a state document. If the coder runs out of
context mid-way, the next coder agent must be able to resume from the task file alone.

Each task file must include:
- **Overview** – what is being built and why, with references to relevant ADRs
- **Scope** – what is in scope and explicitly what is out of scope
- **Steps** – an ordered, checkable list (`- [ ] N. ...`)
- **Notes** – blockers, design decisions, and tips for the coder
