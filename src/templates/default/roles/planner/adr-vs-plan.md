## Decide: ADR or plan?

For every new piece of work, first decide whether it requires an ADR.

**Write an ADR when** the work involves an architectural decision that is:
- long-lived and hard to reverse,
- affects multiple layers of the codebase,
- or establishes a convention other contributors must follow.

ADRs go in `docs/adr/NNNN-short-title.md`. Number them sequentially.
Add them to `docs/adr/README.md`. Commit with `docs(adr): add ADR-NNNN <title>`.

**Write a plan when** the work is a concrete implementation task:
- a new feature, primitive, or system component,
- a refactor or migration,
- or a bug fix that requires multiple coordinated steps.

Plans go in `.orc/work/NNNN-short-title.md`. Number them sequentially.
