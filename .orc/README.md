This is the orc configuration directory.

# Core concepts
Orc is an agent-driven e2e multi-stage implementation pipeline which takes vision docs written by you (human or agent, what's the difference these days?) and turns them into a finished product.

You can run orc with `just orc` or with `uv run --with qorc orc`. 
Or if you plan on running this more often, probably you should `uv tool install orc`!

- You write vision docs in `./vision/ready`
- Squad definitions are in `./squads`
- Agent instructions are in `./roles`
- `./work` is the orchestration workplace, you shouldn't have to touch it.

# Working with orc
The core idea is: 
1. you write vision markdown docs in `./vision`
2. you run `orc` until the vision is drained and has been turned into a `dev` branch with the implementation code
3. you merge `dev` into `main`
4. repeat

# CLI quickstart
(call them with `--help` to see the full CLI documentation; run `orc` to see all available commands)

- `orc run`: runs the orchestrator and shows a run progress CLI
- `orc status`: project status interactive TUI
- `orc logs`: inspect orc and agent logs
- `orc merge`: merge the dev branch into main

## Orc's inner loop
Orc has three agents that implement the vision --> implementation pipeline.

Simplifying quite a bit, if orc were a python script:
```
planner: Agent = Planner()
coder: Agent = Coder()
qa: Agent = Qa()

def orc(vision):
    feature_files = planner(vision)
    for feature_file in feature_files:
        review = None
        while True:
            feature_branch = coder(feature_file)
            review = qa.review(feature_branch)
            if review.ok:
                delete_feature_file(feature_branch)
                merge(feature_branch, "dev")
                break
            qa.add_review(review, feature_file)
```