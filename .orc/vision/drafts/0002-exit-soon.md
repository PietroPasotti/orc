Right now 'orc run' will run until it exits naturally (no more work to do), an exception occurs, or the user Ctrl+C's.
We might want a 'terminate ASAP' option that allows the user to signal to the system that it should stop as soon as possible.

TBD:
- how much of the work can we recover? if we kill a coder mid-flight, will the next coder be able to pick it up naturally or will it waste time?
- what about if we interrupt the orc mid-merge or rebase?

Related:
- should orc be a detached process, so we're less reliant on the shell that originally ran 'just orc'?