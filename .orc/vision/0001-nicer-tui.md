The TUI should contain a layout like so:

- on the top, headers with loop status, backend, telegram link etc...
- then a main layout consisting of three columns with:
  - on the left, a card (floating table) with the planner agent status
  - in the middle, a grid of cards with the coder agent statuses
  - on the right, a grid of cards with the qa agent statuses
  
the columns in the main layout should be labeled with the data that's shared between all agents in that column, namely the type, the model name
