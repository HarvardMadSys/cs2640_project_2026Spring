# Feature Landscape: Final Report + Live Demo

**Domain:** Cache-policy comparison study (systems course final deliverable, CS 2640 Spring 2026)
**Researched:** 2026-04-16
**Scope:** Features/analyses/presentation artifacts that should go into the final report and live demo, benchmarked against published cache-policy papers (SIEVE NSDI '24, S3-FIFO SOSP '23, LHD NSDI '18, TinyLFU, AdaptSize NSDI '17).

**Confidence note.** External web search (WebSearch, WebFetch, gsd-sdk, ctx7) was unavailable during this research pass. Paper-practice claims below are drawn from training-data knowledge of these publications (last updated January 2026). Where a specific figure or practice is cited, it is marked HIGH (directly reported in the original paper, well-known in the community), MEDIUM (general cache-literature convention, likely but not verified against the specific paper), or LOW (plausible but not directly sourced). Any claim the final report cites verbatim from these papers should be checked against the PDFs before submission.

## Table Stakes

Features that every serious cache-policy paper includes. Missing any of these will make a submission look incomplete to a storage-systems reader.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Object miss-ratio curve (MRC) per policy** | Core artifact of every cache-eviction paper since Mattson 1970. SIEVE Fig. 4-5, S3-FIFO Fig. 7, LHD Fig. 5 all lead with this. | Low (already exists) | Plot miss ratio vs. cache size (as % of working set) for all 6 policies, both traces. Log-x axis standard. |
| **Byte miss-ratio curve** | CDN/object-cache papers report both because size-weighted misses are what drive bandwidth cost. S3-FIFO, AdaptSize, LRB all report both. | Low (already exists) | Already computed; keep prominent. |
| **Throughput / ops-per-second** | SIEVE's headline contribution is throughput (it's "simpler and faster than LRU"). Any paper comparing against SIEVE/S3-FIFO that omits throughput will read as incomplete. | **Medium** (new) | Measure `accesses/sec` wall-clock on a single thread for each policy on a fixed trace. Report median of 3-5 runs. No warm-up in the number, but warm the cache first. HIGH confidence this is expected given the extension adds W-TinyLFU. |
| **Workload characterization table** | Every paper opens §4 (Evaluation) with a trace summary: trace name, # requests, # unique objects, working-set size in bytes, Zipf α (measured), OHW ratio. | Low (already exists) | Extend the existing Congress summary table with a PACER row. |
| **Multiple cache sizes** | Never a single-size comparison. Usual convention: 0.1%, 1%, 10% of working set (sometimes also 0.01%, 50%, 100%). The progress report already uses 0.1/1/5/10%. | Low (already exists) | Keep as is; consider adding 50% to show convergence. |
| **Multiple workloads** | Single-workload claims are not credible. SIEVE uses 1486 traces across 6 datasets; S3-FIFO uses 6594 traces. A course project with 2 real-world domains (Congress + PACER) is defensible. | Medium (PACER collection pending) | Congress + PACER + 1 synthetic (Zipf) is the minimum. |
| **Comparison against a baseline set including LRU** | LRU is the "textbook" baseline; anything worth publishing compares against it. Modern papers also compare against ARC, 2Q, LIRS, or at minimum FIFO and CLOCK. | Low (already exists) | Our 6 policies (LRU, FIFO, CLOCK, S3-FIFO, SIEVE, W-TinyLFU) covers the modern comparison set cleanly. |
| **Reproducibility package** | SIEVE, S3-FIFO, LHD, LRB all publish code + trace download scripts. Course grader parity means a `make run && make plots` that reproduces every figure. | Low (mostly exists) | Add a `make reproduce` target; include exact CLI args and seeds. Document trace collection in README. |
| **Clear "winner per regime" statement** | Strong papers close §4 with a summary like "SIEVE wins below 1% cache size and above α=0.8; LRU competitive at high cache size." Vague conclusions read as unwilling to commit. | Low (writing) | Our progress-report findings already support this (SIEVE wins at every size, gap grows with α). |
| **Figures with policy colors + markers consistent** | Publication convention: every policy uses the same color/marker across every figure. | Low (already exists) | `POLICY_COLORS`/`POLICY_MARKERS` dict in `plot_results.py` — extend for W-TinyLFU. |
| **Absolute numbers in text, not just relative** | "SIEVE improves miss ratio by 17%" must be accompanied by the absolute numbers (0.46 vs 0.56) so readers can sanity-check. | Low (writing) | Progress report already does this. |

## Differentiators

Features that set this project apart from a minimal submission and play to its specific strengths.

### Analyses tied to the project's unique angle

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Cross-domain comparison table (Congress vs. PACER vs. generic Zipf)** | The project's real novelty: two public-records domains with different size distributions and endpoint taxonomies. Frame as "does a production CDN-style cache tuned for generic traffic work on legal/legislative APIs?" | Medium | One table per cache size. Highlight where policy ranks *change* across domains — that is the paper's news. |
| **Per-workload Pareto plot: miss ratio vs. throughput** | Standard in SIEVE (Fig. 13) and S3-FIFO (Fig. 8-9). Makes the "simpler = faster" argument visually. W-TinyLFU sits at the "low-miss-ratio, lower-throughput" Pareto corner; LRU/FIFO at the opposite; SIEVE in the middle. | Medium | Needs throughput numbers (see Table Stakes). One figure per trace. |
| **MRC error bands from SHARDS vs. exact** | Lets us *quantify* how trustworthy the approximate curves are. Shaded band around each SHARDS curve showing ±MAE. | Low-Medium | Already have the error data; just overlay on the SHARDS MRC figure. |
| **SHARDS error scaling with trace length** | Directly addresses the "0.1% sampling at 40K = 17 samples" limitation from the midpoint report. Shows MAE as a function of (sampling rate × trace length). This is a good miniature contribution of its own. | Medium | Requires the ≥1M synthetic trace from SIM-12. Plot MAE vs effective sample count; demonstrate the 0.1% MAE-at-1%-sampling figure from Waldspurger FAST '15. |
| **Alpha sensitivity curves per policy per trace** | Shows *why* policies win or lose (it's about skew). SIEVE's advantage growing from 7.6% at α=0.6 to 22.3% at α=1.2 is already a strong finding in the midpoint. | Low (exists) | Run on both traces; put them side-by-side. If PACER has a different natural α, annotate the "real α" as a vertical line on both plots. |
| **OHW sensitivity sweep** | Helps explain W-TinyLFU's expected win (it's designed to filter OHWs via Doorkeeper). Sweep the OHW regime (cold-start-heavy vs. warmed-up trace) and show each policy's response. | Medium | Existing OHW analysis + new per-policy miss-ratio columns keyed to OHW window length. |
| **Size distribution vs. byte-MRC relationship** | Congress has a bimodal tiny-response distribution (median 231B); PACER (legal docs) will likely have a heavier tail. This *predicts* that byte-MRC diverges more from object-MRC on PACER. If the data confirms, that is a clean finding. | Low-Medium | Already have byte-MRC; just needs side-by-side framing. |

### Methodological rigor items (what reviewers look for)

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Ablation: S3-FIFO without ghost queue, SIEVE without visited bit, W-TinyLFU without Doorkeeper** | Shows *which component* of each policy is doing the work. S3-FIFO paper (Yang et al. SOSP '23) includes an ablation of the small/main ratio and ghost queue; SIEVE paper shows the visited-bit reset is the key mechanism. MEDIUM confidence on exact ablations these papers ran; HIGH confidence ablations are expected for modern cache papers. | Medium-High | Pick one ablation per complex policy (not all). Strongest candidate: W-TinyLFU with vs. without Doorkeeper admission, since we're implementing both. |
| **Parameter sweep for S3-FIFO small-FIFO ratio** | S3-FIFO's paper sweeps the 10%/90% split (see their Fig. 10, MEDIUM confidence). A one-figure sweep of 5%/95%, 10%/90%, 20%/80%, 30%/70% on our traces shows the parameter isn't magic — or shows 10% genuinely is optimal. | Low | Already parametrizable in the code; just run the sweep. |
| **Sensitivity to trace length (learning curves)** | Hit-rate-over-time or miss-rate-over-time plots. Shows warm-up behavior and steady-state. W-TinyLFU takes longer to warm because of the sketch; LRU is instant. | Medium | `miss_ratio` per windowed chunk (e.g., every 10K accesses). One figure per policy per trace. |
| **Confidence intervals or multi-seed runs** | Replay-Zipf uses a seed. A serious evaluation reports across ≥3 seeds and shows variance (error bars or shaded regions). Especially matters at small cache sizes where stochastic effects dominate. | Low-Medium | Run each configuration 3-5 times with different seeds; plot mean ± stddev. Cheap because sim is fast. |
| **Reproducibility metadata table** | Appendix: CPU model, compiler version, random seed, trace SHA256, exact CLI commands. Readers can recompute. | Low | Already most of this in the existing Makefile; just document. |
| **AI-use report as first-class deliverable** | Professor explicitly requested this. Treat it as a methodological artifact: what the agent did, what it got wrong, how bugs were caught. The midpoint already has a sketch. | Medium | Separate PDF or appendix. Concrete examples of caught bugs (SHARDS denominator, size-tracking in all 5 policies) are the strongest content. |

### Writing / presentation features

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **"Finding" / "Takeaway" boxes** | S3-FIFO and SIEVE use numbered observations ("Observation 1: ...") to structure the evaluation. Easy to skim, easy to cite. | Low | 3-6 takeaways for the final paper. |
| **Decision tree / guidance section** | "If your workload is X, use Y" closes the paper with practitioner-facing advice. Reads as confident. | Low | One page. Covers high-skew, mixed-size, tiny-responses, cold-start-heavy. |
| **Related-work table, not just prose** | A 1-table comparison of ARC, LRU, FIFO, CLOCK, 2Q, LIRS, TinyLFU, S3-FIFO, SIEVE by (hit-rate-mechanism, overhead, parameter count) is more informative than prose and takes 10 minutes. | Low | Keep to 10 rows. Mark which are ours. |

## Demo-specific features

The live demo is a separate deliverable (DOC-04). Demos are judged on **what they show running in real time** and **how quickly the audience gets the point**. Published-paper artifacts (figures, tables) are not demos.

### Must-have demo features

| Feature | Why | Notes |
|---------|-----|-------|
| **Live policy sweep on a pre-loaded trace** | One command runs all 6 policies and prints the MRC as it goes. Takes <30s for the 40K replay trace. | Use the existing `--alpha-sweep`-style driver. Show the progress table streaming live. |
| **Switchable α parameter** | Bump α from 0.6 to 1.2 and re-run. Audience *sees* the SIEVE/W-TinyLFU advantage grow. | Wrap in a tiny bash script or single flag. Pre-generate traces at a few α values so it's instant. |
| **Switchable trace (Congress ↔ PACER)** | Shows cross-domain robustness live. | Pre-load both traces; `--trace congress.csv` vs `--trace pacer.csv`. |
| **One figure rendered during the demo** | "I'll run this and show you the plot." Tying the live output to the paper's central figure sells the story. | `make plots` completes in <5s if CSVs exist. |
| **Workload characterization printout** | Shows the audience the Zipf α, OHW ratio, size percentiles — sets up why policies differ. | Already prints on every run. |
| **Printed "winner" table at the end** | One visual summary line: "Winner: SIEVE at 1% cache, 0.462 miss ratio vs LRU 0.555". | Already in the per-run output; keep it prominent. |

### Should-have demo features

| Feature | Why | Notes |
|---------|-----|-------|
| **SHARDS vs. exact side-by-side** | Shows the approximation working, ties back to the MRC-construction content in the course. | One `--shards-exact` run. |
| **Per-policy throughput printout** | Reads as a "live benchmark." SIEVE and S3-FIFO are noticeably faster than LRU; the audience will see it. | Add `accesses/sec` to the per-run print. |
| **W-TinyLFU internals visible** | Print Doorkeeper Bloom filter hit rate and Count-Min Sketch memory usage. The "new" component of the system should be visible during the demo. | One extra line in the per-run printout. |

### Nice-to-have / cut-if-time

| Feature | Why | Notes |
|---------|-----|-------|
| **Live animation of cache contents** | Visual of which objects are in cache, which got evicted. Impressive but takes time to build. | Consider cutting. |
| **Interactive α slider** | Nice but not substantively different from re-running with different flags. | Cut unless trivially cheap. |

### Demo risks to pre-empt

- **Timing.** Running `--alpha-sweep` with SHARDS on a 500K trace takes ~2 minutes. Budget for this: either pre-compute and replay from cached CSVs, or use a smaller trace for the live run.
- **Terminal legibility.** Increase font size, pre-`clear` between sections, use a wide enough terminal that the table doesn't wrap.
- **Fallback.** Have all figures pre-rendered as a backup in case the live run fails.

## Anti-Features

Features to explicitly NOT build. The project has limited runway (≈4 weeks) and scope containment is listed as a risk in PROJECT.md.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Production-grade service** | PROJECT.md out-of-scope. Not what the assignment asks for. | Stay a batch simulator. |
| **Multi-tenant or distributed cache benchmarks** | Out of scope per PROJECT.md; this is a single-object-cache study. | Single-cache only. |
| **Real-server latency or tail-latency measurements** | Published benchmarks in this space (Pelikan, Memcached-proxy work) require a running service and realistic client harness. We have neither and adding them adds weeks. | Measure in-process `accesses/sec` only; name it "simulation throughput" not "server throughput." |
| **Real access logs / CDN-trace evaluation** | Would be great, but CDN traces (Wikipedia, Twitter, Meta) require different infra and lose the legislative/judicial-domain angle the project is built around. | Stay with Congress + PACER; note the limitation explicitly (the midpoint report already does). |
| **Cost-based / size-aware admission policies (GDSF, LFUDA, LHD, LRB)** | PROJECT.md out-of-scope beyond W-TinyLFU. Each would be a week+. | Compare against them in the related-work table only. |
| **Additional eviction policies (ARC, 2Q, LIRS)** | Scope creep. Six policies is already more than most course projects. | Discuss in related work; mark as future work. |
| **Machine-learning policies (LHD, LRB, Parrot)** | Out of reach for 4 weeks; LHD alone is a NSDI paper's worth of work. | Cite as the current research frontier and contrast against the "simpler-is-better" SIEVE/S3-FIFO thesis. |
| **Multiple cache-replacement granularities (block vs. object)** | The simulator is object-level only. | Stay object-level; state it explicitly in §3. |
| **Throughput benchmarks under multi-threaded load** | Out of scope per PROJECT.md ("simulator is single-threaded by design"). Thread-safe SIEVE/S3-FIFO variants are separate research questions. | Compare single-threaded throughput only, note lock-freeness as a paper-level claim made by the authors (not ours to validate). |
| **Re-inventing trace collection for a 3rd domain** | PROJECT.md: "A third trace source — two domains are enough." | Stop at Congress + PACER. |
| **Bespoke CDN simulation / TTL modeling / freshness semantics** | Complicates the miss-ratio semantics; every CDN paper makes different choices here. | Treat all accesses as equivalent object reads; note that TTL/freshness is out of scope. |
| **Custom plotting / visualization framework** | `plot_results.py` is fine. Re-doing it in D3 or Plotly burns a week with no deliverable improvement. | Keep matplotlib, tighten typography. |

## Feature Dependencies

```
TRACE-03/04/05 (PACER collection)
  → ANAL-01 (cross-workload comparison)
    → Differentiator: cross-domain table
    → Differentiator: per-workload Pareto
    → Must-have demo: switchable trace

SIM-09/10/11 (W-TinyLFU)
  → Table stakes: all figures include 6th policy
  → Differentiator: Doorkeeper ablation
  → Should-have demo: W-TinyLFU internals

SIM-12 (1M synthetic trace)
  → SIM-13 (SHARDS rigor)
    → Differentiator: SHARDS error scaling figure

Throughput measurement infra (NEW)
  → Table stakes: throughput column
  → Differentiator: miss-vs-throughput Pareto
  → Should-have demo: throughput printout
```

Throughput measurement is the highest-leverage new *infrastructure* item: it supports one table-stakes feature, one differentiator, and one demo feature. It should be added early.

## MVP Recommendation

Rank-ordered by priority for the final submission (assuming ≈4 weeks of runway):

1. **W-TinyLFU implementation + validation** (SIM-09/10/11). Table-stakes: can't ship a "6 policies compared" paper without the 6th. High-risk per PROJECT.md, so front-load it.
2. **PACER trace collection** (TRACE-03/04/05). Table-stakes for the cross-workload story. Also high-risk; front-load in parallel with W-TinyLFU.
3. **Throughput measurement** (new, small). Adds one column to existing MRC tables and enables the Pareto figure. Probably a day of work.
4. **Cross-workload MRC tables + per-workload α sweep + per-workload OHW** (ANAL-01/02). All six policies, both traces, produce the core results. Cheap once (1) and (2) are done.
5. **SHARDS scale validation** (SIM-12, SIM-13). One-day job once the synthetic-trace generator exists.
6. **Final report** (DOC-02). Budget ≥1 week. Writing always takes longer than expected.
7. **AI-use report** (DOC-03). Half a week; midpoint has the sketch.
8. **Live-demo script + dry-runs** (DOC-04). Half a week. At least two full dry-runs before the real thing.

### Defer unless runway opens up

- Ablations (Doorkeeper-off, visited-bit-off): nice-to-have, ~2 days each.
- S3-FIFO parameter sweep: nice-to-have, ~1 day.
- Multi-seed confidence intervals: nice-to-have, ~1 day.
- Learning-curve plots: nice-to-have, ~1 day.

### Explicitly cut

- Any new policy beyond W-TinyLFU.
- Any new trace domain beyond PACER.
- Any real-server or multi-threaded measurements.
- Any rewrite of the plotting framework.

## Sources

**Primary (training-data knowledge; not verified during this research pass due to web access being unavailable):**

- Zhang et al., "SIEVE is Simpler than LRU: An Efficient Turn-Key Eviction Algorithm for Web Caches," NSDI '24. Reported contributions: visited-bit + roving-hand mechanism; evaluation across ~1486 traces from 6 datasets; miss ratio and throughput as headline metrics; comparison against LRU, FIFO, CLOCK, ARC, LIRS, TinyLFU, S3-FIFO. HIGH confidence on general shape; figure numbers above marked MEDIUM.
- Yang et al., "FIFO queues are all you need for cache eviction," SOSP '23 (S3-FIFO). Reported contributions: small-FIFO + main-FIFO + ghost-FIFO layout; parameter sweep of the small-FIFO ratio; evaluation across ~6594 traces (Twitter, Wikipedia, MSR, others). HIGH confidence on general shape.
- Beckmann, Chen, Waldspurger, "LHD: Improving Cache Hit Rate by Maximizing Hit Density," NSDI '18. Reported contributions: economic framing (hit density); comparison against LRU, LRB baselines; reports miss ratio, throughput, and per-trace breakdown. HIGH confidence.
- Einziger, Friedman, Manes, "TinyLFU: A Highly Efficient Cache Admission Policy," ACM ToS 2017, and Caffeine's W-TinyLFU variant (Manes, open-source). Reported mechanism: Count-Min Sketch frequency estimator with Doorkeeper Bloom filter; 1%-window + 99%-main partition. HIGH confidence on mechanism.
- Waldspurger et al., "Efficient MRC Construction with SHARDS," FAST '15. Reports 0.1% MAE at 1% sampling on large traces; this is the target in SIM-13. HIGH confidence.
- Berger et al., "AdaptSize: Orchestrating the Hot Object Memory Cache in a Content Delivery Network," NSDI '17. Reports both object- and byte-MRC; popularized size-aware CDN-cache evaluation. MEDIUM confidence on exact figures.
- Song et al., "Learning Relaxed Belady for Content Distribution Network Caching," NSDI '20 (LRB). ML cache policy; referenced in related work. Out-of-scope for this project but relevant citation.

**Internal sources (HIGH confidence, directly read):**

- `/Users/mirayu/civicache/.planning/PROJECT.md` — scope, out-of-scope list, active requirements, key decisions, risks.
- `/Users/mirayu/civicache/progress.tex` — existing midpoint results, figures, AI-use sketch.
- `/Users/mirayu/civicache/.planning/codebase/ARCHITECTURE.md` — implementation layers, current CSV outputs, existing capabilities.

**Verification flag.** Any cache-paper figure or numeric claim that ends up in the final report should be checked against the paper PDF before submission. This research pass did not have web access and relied on training-data recollection for the external papers.
