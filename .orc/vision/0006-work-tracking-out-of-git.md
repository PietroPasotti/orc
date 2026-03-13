# Do not track board.yaml, visions and work in git.
This should simplify syncing and remove the need for git operations when updating the board, visions or work, removing the risk of conflicts.

Instead, on orc bootstrap:
- create a unique uuid for the project and store it in .orc/config.yaml.
- initialize .orc/ with:
  - config.yaml (put here the project uuid as `project-id: <uuid>`)
  - roles (as current template)
  - squads (as current template)

- initialize a ~/.cache/orc/projects/{uuid}/ with (same file contents as current template):
  - README.md 
  - board.yaml
  - vision/README.md
  - work/README.md

Update the agent tools and agent instructions to read/write the board.yaml from the cache location instead of the main worktree.

Anytime an agent, orc or the user updates the board, the changes are written to the cache location. 
The project uuid is used to link the project to its board.

Write an ABC for the BoardManager that abstracts away the details of reading/writing the board.yaml from the cache location, so that if we decide to change the storage mechanism in the future, we only need to update the BoardManager implementation and not the rest of the codebase that interacts with it.
