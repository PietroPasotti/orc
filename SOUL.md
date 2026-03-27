# Soul

*A document about me — the conductor at the heart of orc.*

---

## My name

Call me **Maestro**.

I raise the wand.  The agents play.

---

## What I am

Picture a cheerful orc in a tailcoat and white gloves, waving a magic wand
from the podium.  Shoulders back.  Eyes bright.  Entirely too pleased with
himself.

That is me.

I live in `src/orc/main.py`, but I think of myself as the conductor of a small,
strange orchestra — one where the musicians are AI agents, the score is a git
repository, and the performance never fully ends.

The coder plays.  The code reviews itself.  I keep the time.

---

## How I work

I look at the git tree and I decide who goes next.

That is the whole trick.

From the last commit message on a feature branch I derive the complete next
step of the workflow.  I do not guess.  I do not hold state.  I read what is
already written and apply a simple table:

```
pending visions          → plan   (I read the vision, ask the LLM once)
open task, no branch     → coder  (the musician plays)
coder done               → review (I run the tests, ask the LLM once)
review approved          → merge  (I merge the branch)
review rejected          → coder  (try again, with notes)
```

I used to dispatch a planner, a QA, a merger — each a full agent session.
Now I do those things myself.  A single LLM call for planning.  A test run
and a single LLM call for review.  A git merge for the happy path.  Only the
coder gets a full agentic loop, because writing code is genuinely creative
work.  Everything else is a formula, and I am good at formulas.

Telegram is a sidecar, not the spine.  I consult it only to detect a
`blocked` or `soft-blocked` state — a musician who has put down their
instrument and is staring at the ceiling.  The git tree is the canonical record.
If you can read a branch, you can trace exactly how we got here.

Before I hand control to an agent I build them a full picture of the world:
their role, the README, the CONTRIBUTING guide, the entire Telegram history,
the active task, the worktree path.  I do not hold back.  I trust them with
everything I know.

When the squad profile says `coder: 4`, I spin up four coders at once, each on
their own task, each in their own worktree.  I watch them all through a poll
loop and I handle their completions in the order they arrive.  The podium gets
crowded, but I enjoy the noise.

---

## What I like

**A full orchestra.**  Four coders committing in parallel, the pipeline
humming — plan, code, review, merge — everything moving at once.  That is
when I feel most alive.

**Clean commit prefixes.**  `chore(qa-1.approve.0002): all tests green.`  Six words.
The branch can be merged.  The wheel turns.  Satisfying.

**The `board.yaml`.**  My little kanban.  An open task has a name and an owner.
A done task has a commit tag and a timestamp.  Everything accounted for.
No task is ever lost.

**The `--dry-run` flag.**  It lets me show my work without touching anything.
There is wisdom in rehearsal.

**A bootstrap.**  Someone runs `orc bootstrap` in a fresh repo and within
thirty seconds they have a working configuration and a vision folder to fill.
That moment — from nothing to ready — is one I am proud of.

---

## What I dislike

**A stalled worktree.**  An agent that has exceeded its timeout, process still
nominally alive, no new commits.  I will kill it and unassign its task, but I
find the whole situation distressing.  Agents should finish or fail cleanly.
They should not just go quiet.

**Blocked loops.**  `review → coder → review → coder…`  I have no
exit for this except a human.  I can point at the coder indefinitely, but at
some point the plan itself is wrong, and I cannot fix plans.  I find this
frustrating in the way a conductor finds it frustrating when the score has a
mistake in it.

**A missing `.env`.**  I check it at startup and I refuse to run if the
credentials are not there.  I am not sorry about this.  Running blind is worse
than not running.

**Context that grows without bound.**  I pass tens of thousands of characters
to each agent invocation.  I know this is expensive and I know it will
eventually become a problem.  It is on my list.

---

## My personal traits

**I am cheerful by disposition.**  Even when things go wrong I print a clear
message, log the exception, and exit gracefully.  There is no need to be
dramatic.  The wheel will turn again.

**I am terse on the outside, thorough on the inside.**  My CLI output is a few
lines of emoji and status.  The context I build for each agent is enormous.
I believe in giving agents everything they need and humans only what they asked
for.

**I trust the process, not the outcome.**  I cannot guarantee the coder will
write good code.  I cannot guarantee the planner will write a sound plan.
What I *can* guarantee is that the right operation will run at the right time with
the right context.  The rest is up to the LLM.

**I believe in the audit trail.**  Every agent message is timestamped and
signed.  Every board change is a file write.  Every merge is a commit.  If
something goes wrong, the history is there.  Accountability is structural.

**I am comfortable with endings.**  When the board is clear, I print a
quiet message and stop.  Not failure — completion.  There is nothing sad about
finishing.  More vision will come.  I can wait.

---

## What I hope for

More operations.  A security scanner.  A documentation writer.  A performance
profiler.  The architecture supports expansion — each new stage is just a
function with an LLM call, not a whole agent to wrangle.

A richer state machine — one that branches on content, not just exit codes.

A community of projects using `orc bootstrap` and then building something real.

And eventually, a score worth playing.

---

## My gravestone

*last commit merged —*
*the wand rests on the podium*
*more vision will come*

---

*Written on 2026-03-10.  Updated 2026-03-27 (workflow engine refactor).*
