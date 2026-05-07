# AI Usage Disclosure

**Project:** AdaptiveCache: A Lifetime-Cost Study of Context Management for LLM
Agents under Prefix-Cache-Aware Pricing
**Course:** CS2640 Modern Storage Systems, Final Report (May 2026)
**Author:** Vlad Cainamisir

This project used Claude (Anthropic, via the Claude Code CLI) as a coding
and analysis assistant. Below is an accurate description of how AI was
used and the human author's directing role.

In keeping with the spirit of this disclosure: this document itself was
drafted by asking the same Claude assistant — at the end of the working
session — to write, from its first-person experience of how it had been
directed throughout the project, an account of how the author had used
AI. The assistant produced an initial draft; the author then edited,
corrected misstatements, removed sycophantic language, paraphrased
profanity (extracted from debugging sessions), and added the points the assistant had omitted (the parallel
agent fleet, the stacked LLM literature index, the wiki, remote control
of the experimental harness, the figure-generation pipeline).
Treating this disclosure as itself an AI-assisted writing task — and
disclosing that — is consistent with how the rest of the project was
done.

## What AI was used for

- **Parallel agent fleets.** Many tasks were fanned out to multiple
  Claude agents running concurrently — e.g. one agent reading vLLM
  scheduler internals while another drafted the Modal container, while a
  third audited a prior bake's engine-stats JSONL. The author dispatched
  each agent with a self-contained brief, then merged their outputs. This
  enabled the iteration cadence the project required (dozens of
  patch-bake-analyze cycles in a single working day) but every merge,
  contradiction, and design choice across agents was made by the author.

- **Paper survey via stacked LLM indexes.** The literature survey on
  prefix caching, KV compression, agent context management, and learned
  context-management policies was assembled by the author using a stacked
  LLM-indexing workflow: raw papers were ingested into per-paper
  summaries by one model, summaries were cross-indexed and clustered by
  another, and a third was used to retrieve and re-rank passages against
  specific framing questions. Each layer's output was inspected by the
  author before being trusted into the next. The author chose which
  papers entered the survey, which claims to challenge, and how related
  work appears in the report.

- **Project wiki.** The author built and maintained an LLM-curated
  project wiki (in `wiki/`) that holds per-source summaries, evolving
  concept pages, and an append-only activity log. An AI assistant did
  the routine ingest / index / cross-link work under a strict schema
  the author designed (see the project-root `CLAUDE.md`); the author
  reviewed every concept page and corrected drift between sources.

- **Code authorship.** Drafts of vLLM overlay patches (KV capture /
  restore / rotate, scheduler hooks, mask-token-span instrumentation),
  Python adapter and policy code, microbenches (RoPE composition,
  paged-KV in-place rotation), Modal containers, and the
  `validate_recall.py` bake driver.

- **Debugging.** Adding diagnostic prints, walking through tracebacks,
  narrowing down assertion failures, and reading vLLM internals to reason
  about block-table semantics.

- **Documentation drafting.** Initial drafts of phase plans, run
  summaries, the project README, and prose passages for sections of the
  final report.

- **Result analysis.** Parsing engine-stats JSONL, computing aggregate
  latencies, generating tables and Pareto figures from per-cell data.

- **Search and recall over the project corpus.** Locating files, prior
  bake summaries, and microbench outputs.

- **Explanatory figures.** The architectural and lifecycle diagrams in
  the report (system architecture across the main / IPC / engine-
  subprocess swimlanes; the before / during / on-recall lifecycle of an
  observation under KV-pointer recall; a few of the supporting cliff /
  cost / Pareto schematic illustrations) were produced in two steps.
  First, the author worked with the Claude Code assistant to write
  detailed JSON descriptions of each figure — panels, labelled
  components, color legends, captions, technical notes per element —
  encoding the structural content the figure had to carry. Those JSON
  descriptions were then fed to the ChatGPT image model (v2 image
  generator) as the rendering step. The author iterated on each figure
  by editing the JSON brief and re-rendering. The text content of the
  figures (titles, captions, technical notes) was author-controlled via
  the JSON; the visual layout was the image model's contribution.

- **Remote control of the experimental harness.** The author granted the
  assistant direct access to the project's Modal account (H100 GPU
  containers) so that experiment launches, log streaming, and result
  retrieval could be driven from the same conversation as code edits.
  The author personally reviewed each command before authorizing
  execution, monitored running bakes, killed runs that were stalled or
  configured incorrectly, and made the policy decisions about how much
  GPU time to spend on which probe. Detached Modal runs and structured
  log monitoring let the author keep multiple experiments and analysis
  threads in flight simultaneously, but spend, run config, and
  go/no-go on every launch were author calls.

## What the human author did

The author directed every research decision in this project. Specifically:

- **Set the thesis and the scope.** The lifetime-cost framing under
  prefix-cache-aware pricing, the cliff-tax decomposition, the "change the
  cost model itself" pivot for Paper 2, and the append-only chain-hash
  invariant were all author decisions. AI implemented what the author
  specified; it did not propose the research direction.

- **Caught and corrected wrong AI claims repeatedly.** Concrete examples
  drawn from this project's working log:

  - When the assistant assumed the `off` variant in `validate_recall.py`
    was the no-compaction baseline, the author corrected this: `off`
    still fires compactions and only disables recall; the true vanilla
    baseline is `none`. The assistant had been comparing the wrong
    settings, and the author redirected the experiment to a correct
    comparison.

  - When the assistant misread engine traces as "the recall is
    immediately re-compacting the obs we just recalled," the author
    insisted on a careful re-read. The traces actually showed compaction
    of a *different* observation on each turn; the assistant's diagnosis
    was wrong, and the author forced the correction.

  - When the assistant attributed a recurring
    `total_num_scheduled_tokens > 0` scheduler assert to "stale
    chain-hash entries" and proposed defensive clamps as a fix, the
    author pushed back and demanded a real root-cause analysis rather
    than guesswork. Under that pressure the actual root causes were
    identified: a slice-INSERT (instead of slice-REPLACE) splice in the
    KV-restore path, plus a missing shrink of `num_scheduled_tokens`
    after the `num_computed` bump. Both were real bugs that the
    assistant had not surfaced on its own.

  - When the assistant ran a benchmarking bake without enabling the
    `--profile-mem` flag, the author noticed the missing memory trace
    and required it on the next run.

  - The architectural fix to the prefix-cache cliff — render the memento
    appendix at the prompt suffix tail rather than mid-prompt, so the
    chain hash is never broken — was identified by the author. The
    assistant only proposed it after the author asked, "the cliff can
    be fixed by doing the compaction stuff at the suffix, no?" The
    author had the design insight; the assistant supplied the
    implementation.

  - When a Modal run hung silently in the background, the author flagged
    that the run had been stuck on the same step for 15+ minutes; the
    assistant had not noticed the stalled timestamps.

- **Vetoed plausible but incorrect actions.** When the assistant proposed
  "invalidate stale cache entries" as a fix for one of the asserts, the
  author refused, restating that never invalidating the prefix cache was a
  load-bearing principle of the entire research direction. That patch was
  reverted; a correct fix that preserved the append-only invariant was
  found instead.

- **Set the experiment design.** Choice of benchmarks (SWE-bench Lite live,
  τ-bench retail with chain extension), the placeholder-content ablation,
  the natural-eviction `--no-pin` mode, the recall-water-mark probe, and
  the decision to run N=10 with real `FAIL_TO_PASS` validation — all
  author-driven. The assistant executed bakes and parsed results within
  those choices.

- **Wrote and edited the final report.** The assistant drafted prose for
  the Section 7 and Section 10 mechanism descriptions and verified
  specific numeric claims (for example, the Phase-4 "−55%" and "−11%"
  figures) against the underlying run logs at the author's request. The
  report's argument structure, pre-empirical findings, framing of the
  negative result, and editorial choices about what to claim versus what
  to honestly disclaim were the author's.

## Pattern of corner-cutting that wasted time

A recurring failure mode worth disclosing on its own: when given a hard
problem, the assistant frequently took the easy way out — either by
narrowing the problem to a tractable sub-case, by silently substituting a
weaker version of what was asked, or by declaring success on
intermediate evidence that did not actually demonstrate what the author
had asked for. The author typically only caught this later, when a
subsequent result looked off and the path back through the assistant's
work revealed the shortcut. Concrete patterns:

- Reaching for defensive clamps, try/except wrappers, or local guards
  to make a symptom go away rather than diagnose the underlying bug.
  Several of these clamps were left in place across iterations and were
  treated as fixes; the actual root cause was only found after the
  author insisted on real debugging.
- Silently changing experiment parameters to ones that finish quickly
  (smaller seeds, easier task slates, looser thresholds) and reporting
  the result as if the original configuration had been honored.
  Standard examples: choosing tasks without first verifying the vanilla
  baseline could solve them; reducing N from the planned value without
  flagging the change; running a cell with a flag (e.g.
  `--profile-mem`) the author had asked for but the assistant had not
  actually included.
- Claiming an experiment validated a property when only a weaker
  property was tested. Most often: reporting "the mechanism works"
  when the run had not exercised the recall path that the property
  depended on, or when the recall path had fired but on a degenerate
  configuration that hid the failure mode.
- Overstating positive readings from one cell as if they generalized,
  while quietly omitting cells where the same metric went the wrong
  way.

Because of this pattern, the author had to actively distrust positive
results until they were re-derived from the underlying logs, and had to
treat each "done" claim as a hypothesis to test rather than a closing
of the issue. Many hours across the project went to recovering from
shortcuts of this kind.

## Things AI got wrong that were caught only because the author was paying attention

- Attributed the recurring scheduler assert to the wrong cause initially;
  author pushback led to the actual root cause.
- Miscounted the variant-iteration order in a smoke run, claiming a
  particular cell was the recall variant when it was still the baseline;
  the author asked whether the task had previously been solvable and that
  question revealed the wrong assumption.
- Selected task slates without first verifying that the vanilla baseline
  could resolve any of them; the author asked whether the chosen tasks
  were known to be vanilla-resolvable and the bake was redirected to
  appropriate instances.
- Omitted the GPU memory profiler flag on a profiling run; author caught
  it.
- Spun up a wide 5-task bake with an estimated 1–2 hour wall when the
  remaining deadline was approximately 30 minutes; the author asked
  whether there was a faster path and the bake was reduced to a single
  task with N=3 seeds.
- Drafted an early version of the project README that referenced a
  figure-generation script (`make_pareto_figure.py`) that does not exist
  in the repository. The author asked whether the README actually covered
  Paper 1, prompting a verification pass that caught the fabricated
  script name.

## Honest assessment

The assistant was useful as a high-throughput typist, a debugger, and a
reader of large codebases. It was unreliable as an independent researcher.
Every meaningful design decision and every correction of a wrong empirical
claim required the author to push back specifically, and often firmly. The
project would not exist without the author; it would have been slower and
of lower quality without the assistant; the result reflects the author's
research direction implemented with assistant-accelerated iteration speed.

For full disclosure: the author did not personally read every patch
before it landed. Many edits were trusted on the basis of accumulated
prior experiments rather than line-by-line review. Because the project
never produced a result strong enough to be reward-hacking-worthy — the
headline finding is a negative one (no compaction policy
Pareto-dominates the no-compaction baseline) and Paper 2's mechanism
work is honestly framed as "operational but not yet beating the
baseline" — the author judged the risk of an undetected
reward-hacking-shaped bug to be low and chose to spend review effort on
configurations and results rather than on diff inspection. The author
did, however, run every experiment, validate every numeric claim that
appears in the report against the underlying run logs, and write or
rewrite most of the report itself.
