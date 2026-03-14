# Vision

This folder contains vision documents for the project.

Vision documents are the source of truth for _what_ to build. The planner agent
reads them and translates each piece of work into either an ADR (`docs/adr/`) or
a task (stored in the project cache alongside this directory).

In principle this is the only place you need to provide input to the system.

## Format

Each vision document is a markdown file describing a feature, system, or
product direction. There is no strict format, but a good vision document
includes:

- **Title** – a short, descriptive title, ideally with a descriptive standard filename like `NNNN-short-title.md` for easy reference and priority sorting
- **What** – the feature or capability being described
- **Why** – the motivation and value for the user/project
- **Constraints** – things that must be true of the implementation
- **Out of scope** – things explicitly not included

## Getting started

Add `.md` files to `./ready` describing what you want to build. The planner will pick
them up on the next `orc run`.
When the planner translates a vision document into work, it will move the source file from `./ready` to `./done`.
If you need a staging area for work-in-progress vision documents, you can use the `./drafts` folder and put your visions there until they're ready for the planner.
The planner only looks at `ready` and only writes to `done`.
