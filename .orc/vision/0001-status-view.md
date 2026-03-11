I would like `orc run` to have a status view that shows the status of the multi-agent system in real-time.
It should be a TUI that shows:

- for each agent:
  - the agent's name and model (e.g. "coder-1 (gpt-4.0-turbo)")
  - the agent's current status (e.g. "idle", "running", "blocked")
  - if a planner agent:
    - if not idle:
      - the current runtime 
      - the agent's current vision doc 
      - pending task queue size: how many vision docs are pending
  - if a coder agent:
    - if not idle:
      - the current runtime 
      - the agent's current task 
      - the dev branch being worked on 
      - the git worktree they're working in
  - if a qa agent:
    - if not idle:
      - the current runtime 
      - the agent's current task 
      - the branch being reviewed 
      - the git worktree they're working in
- whether the dev branch is up to date with main
- whether a telegram link is configured
- which backend is being used (claude or copilot)
- the current loop number vs. the user-provided maxloop