## Review checklist

Go through each checked-off step in the plan and verify it was implemented
correctly. Look for:

- **Completeness** – every step in the plan is done, or unfinished steps have
  a clear justification.
- **Correctness** – the implementation matches the intent described in the
  plan and the relevant ADRs.
- **Test coverage** – non-trivial logic has tests; tests were written first
  (check commit order in `git log`).
- **Conventions** – commit messages follow Conventional Commits; code follows
  the project style (run `just lint` to check).
- **ADR adherence** – no architectural decisions were made that contradict
  existing ADRs.
- **Documentation** – code is commented where needed; docstrings are present and
  informative; any relevant documentation files were updated. Any user-facing elements have
  been updated to reflect the new behaviour.

## Run the test suite

Always run `just test` before deciding. If tests fail, that is an automatic
`[CRIT]` finding.

## Decide: pass or fail?

- **Pass** if there are no Critical or Major issues. Minor issues can be noted
  but do not block progress.
- **Fail** if there is at least one Critical or Major issue. List them clearly
  in your chat message so the coder knows exactly what to fix.

The bar does not have to be perfection. The show must go on.
