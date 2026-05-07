# AI Usage

## How I used AI

The primary tool was **Claude Code**, Anthropic's CLI agent for software
engineering. I drove it from the project's working tree, with a short
`CLAUDE.md` at the repo root that pinned down build commands, run
conventions, and a one-paragraph architecture summary so the agent did not
need to rediscover the project on every session.

The work followed a tight loop: I proposed an idea or hypothesis, the agent
implemented it, ran the sweep, drafted a structured report, and I reviewed
both the diff and the writeup before committing. Concretely, I leaned on
the agent for:

- **Translating algorithm ideas into code** — turning a sentence-level
  description of an eviction policy or controller into a working subclass
  with the right hooks into the simulator and the vLLM block manager.
- **Running the sweep grid** — algorithm × trace × cache-size combinations
  through the Makefile in parallel, then parsing logs into the tables that
  fed each report.
- **Writing experiment reports** — using a fixed template (Idea, Design,
  Setup, Results, Conclusion) so reports stayed comparable week to week.
- **Cross-repo investigation** — reading specific files in upstream
  references when our numbers diverged from what their docs claimed.
- **Routine engineering** — Makefile fan-outs, refactors of shared
  helpers, plot scripts, smoke runs under AddressSanitizer, and the
  bookkeeping involved in renaming policies as the design evolved.

The agent was especially valuable on the *hardest* implementation
details, not the easiest ones. Tasks like the multi-hole filling logic
in the block manager — which required tracking several interleaving
invariants across the scheduler, the block pool, and the eviction policy
— were where it saved the most absolute time. The fiddly state-machine
reasoning that I would otherwise have spent days on, with off-by-one
bugs surfacing later in production, the agent got close to correct on
the first attempt and fully correct after one round of review.
Boilerplate, by contrast, was a wash: it is fast for me too, and the
agent's version still needed reading.

What stayed human: research direction, hypothesis formation, deciding
which sweep results were meaningful, and the final analytical claims in
the writeup. The agent was a force multiplier on execution, not on
judgment.

## What I learned

**A short, project-specific instructions file is worth more than long
prompts.** Once I wrote down build commands, file layout, and the
non-obvious conventions in `CLAUDE.md`, prompts shrank from paragraphs to
single sentences and the agent stopped guessing wrong about where things
lived. The investment paid back within a day.

**Structure your asks around outputs, not steps.** Saying "produce this
table on these traces and tell me which signal moved in the wrong
direction" worked far better than "first do X, then Y." The agent picks
the steps; I own the outputs and the review.

**Treat "tests passed" as a hypothesis, not a result.** The agent is
optimistic by default. I had to verify numbers, re-run sweeps, and read
diffs myself before believing the cheerful summary at the end of a turn.
This is the single most important habit I built.

**The agent does not think as deeply as a human, so the crucial ideas
have to come from me.** It is excellent at executing within a frame I
have already set, and it can produce plausible variations on an existing
idea, but it does not push past the surface to ask why something works
or to reframe a problem from a different angle. Every load-bearing
insight in this project — the choice of signal, the realization that
freq should persist across promotion, the decision to anchor the ratio
floor — came from me staring at plots or reading the code, not from the
agent. As of now, treat the agent as a fast hands-and-eyes layer; the
ideas have to be yours.

## What I did not expect

**The agent caught a real bug in upstream code.** While cross-checking our
port against the reference implementation, it noticed a logic error
(conflating two different reasons for the same callback) that the
upstream authors had missed and instrumented a fix to confirm. I expected
AI to help with our code; I did not expect it to be a useful second pair
of eyes on third-party code.

**Verifying took longer than I budgeted.** A naive estimate would say AI
made me 5-10x faster. The real number, after accounting for the time I
spent reading diffs, re-running sweeps, and pushing back on stale
references, was closer to 2-3x. Still a large win, but not the headline
number.

## Tips I would give a teammate

- **Write a `CLAUDE.md` (or equivalent) on day one.** Build commands, run
  commands, file layout, project-specific conventions. Half a page is
  plenty. Update it when the agent gets confused — the confusion is a
  signal that the file is missing something.
- **Date everything and commit reports next to the code change they
  describe.** This makes it convenient to see the difference
  and keep track of what you have done on each day.
- **Verify numbers, not narratives.** Always re-derive the headline
  number from the logged sweep yourself. The narrative around it is
  usually fine; the numbers occasionally are not.
- **Push back early.** If a turn produces something that smells off,
  correct the agent immediately rather than letting it accumulate. The
  context window is short and the cost of a redo grows fast.
- **Keep research direction and hypothesis formation off the agent's
  plate.** Use it to test ideas, not to generate them. Ideas it generates
  tend to be plausible-but-derivative; the interesting ones came from me
  staring at plots.
- **For cross-repo or unfamiliar-codebase work, give it specific entry
  points.** Vague "investigate X" prompts produce vague summaries.
  "Read these three files, answer this specific question" produces
  precise answers.
- **Run the same sanity tests after AI work that you would after your
  own.** Sanitizer builds, the smallest end-to-end smoke, a manual
  spot-check of one log. None of these are optional.

The overall lesson: AI dramatically lowered the cost of *doing* an
experiment, which made it cheap to try ideas I would otherwise have
deferred. The bottleneck shifted from implementation to deciding which
experiments were worth running and reading the results carefully. That
shift is the actual story of this project.
