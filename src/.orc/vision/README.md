# Vision

This folder contains vision documents for the project.

Vision documents are the source of truth for _what_ to build. The planner agent
reads them and translates each piece of work into either an ADR (`docs/adr/`) or
a task (`orc/work/`).

## Format

Each vision document is a markdown file describing a feature, system, or
product direction. There is no strict format, but a good vision document
includes:

- **What** – the feature or capability being described
- **Why** – the motivation and value for the user/project
- **Constraints** – things that must be true of the implementation
- **Out of scope** – things explicitly not included

## Getting started

Add `.md` files here describing what you want to build. The planner will pick
them up on the next `orc run`.
