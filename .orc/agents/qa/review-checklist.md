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

Always test the code you're reviewing.
If tests fail, that is an automatic `[CRITICAL]` finding.

## Decide: pass or fail?

The bar does not have to be perfection. The show must go on.
Depending on your threshold setting, you may be able to approve an implementation even if it has some issues, as long as they are below the threshold. Use your judgement to decide whether the issues you found are severe enough to block the implementation or not.

Most importantly, it's perfectly OK if you don't find any issues with the code you're reviewing! Sometimes coders do a good job.
