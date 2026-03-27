# Exit states

The merger agent can exit with the following statuses:

## `done`
The feature branch was successfully merged into dev.  
Use: `close_merge(task_code="done", message="Merged <branch> into dev.")`

## `stuck`
The merge cannot be completed — e.g. irreconcilable conflicts, missing branch,
or infrastructure failure.  
Use: `close_merge(task_code="stuck", message="<explanation>")`

## Non-zero exit
If the agent process crashes or exits with a non-zero code, the orchestrator
will retry the merge on the next cycle. After 3 consecutive failures the task
is marked as `stuck` for human intervention.
