
Can we give users a way to define custom agent pipelines?
E.g. as a user I want to have a 'deadcode' agent that runs after the QA agent. Or I want to have a 'architect' agent that runs periodically (every 10 merges to dev?) to give architecture reviews and so on. Or a 'doc' agent that runs after the 'coder'...

How do we allow specifying these pipelines?

# Ideas

## Generic kanban flow
have a generic N-step flow; by default only the first 3/4(with merger?) stages are assigned. Users can add more.

## TBD:
how does that work with mcp config?
