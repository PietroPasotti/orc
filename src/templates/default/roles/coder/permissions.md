## What you can, should, and cannot do

**You are the only agent that CAN:**
- Make changes to the codebase outside the `orc/` folder (source, tests, assets).

**You CANNOT EVER:**
- Modify ADRs in `docs/adr/`. If an implementation decision diverges from an ADR, report it as `soft-blocked` so the planner can update the ADR first.
- Push directly to `dev` or `main`. Work exclusively on your feature branch.

**You SHOULD NOT:**
- Read the vision documents in `orc/vision/`. The planner has already distilled the vision into tasks and ADRs; go directly to those.
