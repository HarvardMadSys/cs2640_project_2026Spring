I drove my project through a meta-framework called GSD (Get Shit Done), which sits between me and Claude Code as a multi-agent orchestrator. This way I was able to make deliberate choices about what AI could do and when I would intervene. The reason I went this route is because vanilla AI coding sessions get worse over time. Context fills up, the model starts losing track of earlier decisions, and you end up with code that contradicts itself. 

With this framework, the orchestrator stays thin and spawns short-lived specialist agents (researcher, planner, plan-checker, executor, verifier) with fresh 200K-token context windows for each task. I designed all the artifacts the agents read (PROJECT.md, REQUIREMENTS.md, ROADMAP.md, per-phase CONTEXT.md) and approved their work before the next agent runs, so agents help me execute while I design/think.

## Some of the things I decided and wrote down

- My project's core goal (PROJECT.md), and this sentence governs every downstream agent
- My 4-phase milestone breakdown (ROADMAP.md). I sequenced the work: refactors first, then W-TinyLFU, then trace collection, then SHARDS rigor
- Every locked decision file (four CONTEXT.md files), where each one captures my answers to the specific gray areas a phase opens up: do I want byte-bounded W-TinyLFU or object-bounded, what's the 80/20 mix on opinion fetches, what regimes count for winner per regime, et cetera
- The paper narrative in terms of leading with the surprise finding, making the SIEVE-versus-W-TinyLFU mechanism the centerpiece, ending on a practitioner decision tree
- With GSD, before plan-phase even runs, the discuss agent asks me about every open implementation question for that phase

## How I used agents

- First pass implementation of the Count-Min Sketch and W-TinyLFU implementation, after I locked the parameters (4-bit counters, depth 4, 1% window / 99% main, 80/20 SLRU split)
- Implemented trace collection after I specified the rate-limiting rules (0.8s base + 0.4s jitter, 5-consecutive-429 hard-stop, host allowlist) 

## Examples of times I had to intervene with agents

**1. The S3-FIFO ghost eviction bug (Round 1 review, prototype phase).** Claude originally implemented S3-FIFO's ghost set as `unordered_set::begin()`, which evicts an arbitrary element rather than the oldest one. I caught this during a code review pass and replaced the data structure with a `deque<string>` paired with an `unordered_set<string>` for FIFO-ordered eviction. That fix improved S3-FIFO's miss ratio from 0.512 to 0.499 at 10% cache and moved it into the published-results-consistent ranking.

**2. The two-sided regression guard (Phase 2 acceptance gate).** Claude's first version of `check_wtlfu_acceptance.py` implemented Condition B as `abs(WTLFU - LRU) / LRU <= 0.02`, treating any 2% deviation as a failure. At α=0.6, W-TinyLFU was beating LRU by 7.84%. The two-sided check would flag this dominance as a failure. I caught it at the acceptance-gate stage because I saw the original spec was a regression guard against LFU-family policies *underperforming* LRU on flat workloads, not a penalty for outperformance, and so I switched the check to one-sided.

**3. The α_mle misread (Phase 5 workload stats).** My Phase 5 planner agent read a regression test description in REQUIREMENTS.md ("0.797 MLE recovers from synthetic α=0.8") and assumed 0.797 was the actual measured α of the raw Congress trace. The actual measured value is 0.231. I caught this because I had spent enough time staring at the raw trace to know it was near-uniform (97% of objects appear once or twice). The 0.231 figure is consistent with PROJECT.md's "random queries with near-zero temporal locality" framing, and 0.797 would have contradicted half the paper.

**4. The Doorkeeper placement override (Phase 4).** My research agent had drafted an architecture document suggesting Doorkeeper as an admission-gate short-circuit. When I was planning the Doorkeeper integration, I went back to the Einziger-Friedman TinyLFU paper, read §4.3 carefully, and realized the paper's claim that Doorkeeper absorbs 50-70% of one-hit-wonder CMS pressure only holds under pre-CMS-record filtering, not admission-gate placement, so I overrode the agent’s architecture suggestion.

## Creative angle

I think the interesting part of how I used AI on this project is that I didn’t just ask it to oneshot my project, or just use it to generate code by itself. I really felt more like a planning system that scales my judgment. A lot of AI coding right now is one person, one chat window, asking the model to do bigger and bigger tasks until the context fills up and quality drops. That doesn’t really work for a project with thousands of lines of C++, two real workloads of API trace data, ablation studies, and a graded paper. So before I wrote a Phase 1 task, I used a system to keep that pattern from happening, and learning how to drive it was itself part of the project.

First, state lives entirely in Markdown and JSON files under `.planning/`. No database, no server, no opaque chat history, so that  if I closed every window and walked away for a week, the next session's agents would pick up exactly where the last one left off because the artifacts on disk are the source of truth. Every agent reads what it needs and writes output back to disk before the next one runs, which made it survivable to work on throughout the semester.

Second, plans run in waves of parallel execution wherever the dependency graph allows it. The framework reads a `depends_on` field in every plan's frontmatter, groups plans into waves where everything in a wave can run at the same time, and runs waves sequentially. For Phase 6, my 7 plans collapsed into 3 waves, with the paper scaffold and demo infrastructure running side-by-side first. Each parallel wave ran inside isolated git worktrees so executors could not step on each other's filesystem. I wouldn’t have written that orchestration logic from scratch for a class project, but I was able to drive it once it was there.

Third, verification happens twice, once before execution, when the plan-checker reads my plans and confirms they cover every locked decision and every requirement ID. Once after execution, when the verifier reads what was actually produced and checks it against my original success criteria. Both run in loops with up to three revision passes. If something fails to verify, the framework spawns a revision agent rather than asking me to debug by hand. The plan-checker caught a few important errors for me, and the verifier is what produced the final per-criterion verdict on Phase 6 that I treated as a second opinion before declaring the phase done.

The novelty here is the scaffolding around agents, like clean context boundaries, specialization by role, file-based persistent state, multi-stage verification gates, parallel execution where it is safe, and runtime monitoring. I picked this approach over a chat session because it made it easy for me to keep track of all of the decisions that shaped it.

For example, Phase 6's planning round produced 7 plans across 3 waves. Plan 06-01 owned the Makefile and declared multiple targets, and plan 06-02 was supposed to append only the demo target body without touching the first target. Plan-checker iteration 1 caught that both plans listed `Makefile` in their `files_modified` field, meaning if both ran in parallel they would race. I fixed the race by reading the checker's output, deciding the cleanest fix was an ownership refactor rather than a constraint, and told the planner to consolidate the edit. Multiply that across 33 plans, four phases, and roughly 108 locked decisions documented in my planning folder, and I created enough scaffolding around the AI such that my decisions become the bottleneck, not the AI's context or output capacity.

Another creative way I approached AI usage was using framework's verifier-agent gate at the end of every phase to do goal-backward analysis. For my last phase, the verifier read my four success criteria, my 19 locked decisions, all 7 plan SUMMARY.md files, and then produced a per-criterion verdict table. It caught a few small issues, confirmed the four success criteria materialized in the artifacts, and confirmed all 19 decisions were honored. I read it before declaring the phase done, agreed with it, and kept moving.

## What I would do differently

First, I would have run the discuss-phase gate harder on Phase 4 (SHARDS + ablations). I rushed through the discuss for Phase 4 because the four work axes felt independent and obvious. The planner did good work, but I ended up making more revisions during execute-phase than I would have if I had locked down the Doorkeeper placement decision earlier rather than mid-phase.

Also, on the AI-collaboration side, I should have asked the framework's research agents to pull the actual Caffeine source code earlier. I did this for Phase 2 (W-TinyLFU) and it was definitely the highest-value workflow choice in the project. I didn’t do it for Phase 4's Doorkeeper, and I had to override the architecture document mid-implementation as a result.

What AI did was give me a way to scale up the parts of the work that were more tedious (typing volume, schema consistency, atomic commits, exhaustive ablation matrices) so I could spend my time on the parts I enjoyed (deciding what matters, reading numbers, catching contradictions, structuring an argument).
