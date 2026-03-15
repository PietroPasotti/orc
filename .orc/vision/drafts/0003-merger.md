Right now the dispatcher calls _drain_merge_queue and waits synchronously for the merge to complete, even if that means dispatching an agent to fix any conflicts.
That's bad for 2 reasons:
- it blocks any other work
- spawned agents don't count against the call budget!

Solution:
- introduce a 'merger' agent role, add it to the default squad with count 1.
- dispatch the merger agent like any other agent.
- display it in the run TUI right under the 'coder'.

BD: merger exit states and instructions (should we allow it to set **blocked**? probably only **stuck**: who's going to recover a blocked merger?)