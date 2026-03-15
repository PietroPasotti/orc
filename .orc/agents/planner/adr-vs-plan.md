## Decide: ADR or task?

For every new piece of work, first decide whether it requires an ADR or it is ready to become a task, that is, an implementation plan.

**Write an ADR when** the work involves an architectural decision that is:
- long-lived and hard to reverse,
- affects multiple layers of the codebase,
- or establishes a convention other contributors must follow.

**Write a task when** the work is a concrete implementation task:
- a new feature, primitive, or system component,
- a refactor or migration,
- or a bug fix that requires multiple coordinated steps.

\