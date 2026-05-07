# civicache — CS 2640 Final Project

## What This Is

A C++17 cache eviction policy simulator that compares five policies (LRU, FIFO, CLOCK, S3-FIFO, SIEVE) on legislative API workloads. The base simulator is already complete: all five policies are implemented, validated against each other, and exercised on a 20K-request Congress.gov trace with replay-Zipf popularity overlay. SHARDS miss-ratio-curve construction is implemented and validated against exact stack distances (2% MAE at 10% sampling).

This PROJECT.md covers the **remaining work**: extending the evaluation to a second domain (PACER court records), adding a sixth policy (W-TinyLFU), validating SHARDS at larger scales, and producing the final deliverables (paper, code, AI-use report, live demo).

## Core Value

The ONE thing that must work: a defensible, well-analyzed comparison of cache eviction policies on **real legislative/judicial API workloads** that produces a clear finding (which policies work, which don't, and why), packaged as a submission-ready report with reproducible code.

Everything else — second trace, W-TinyLFU, SHARDS rigor — strengthens this core story but isn't the story itself.

## Context

- **Course:** CS 2640 (Modern Storage Systems), Spring 2026
- **Deadline:** End of semester (4+ weeks runway)
- **Existing artifacts:** Full simulator, 20K Congress.gov trace, replay-Zipf results, progress report, PROCESS.md, codebase map
- **Professor feedback on midpoint:** Positive on design; flagged that client-generated traces are a compromise (already mitigated by replay-Zipf); explicitly asked for an AI-use report
- **Built so far with:** Claude Code as an implementation collaborator; three rounds of code review caught 9 bugs

## Requirements

### Validated (existing capabilities)

- ✓ **SIM-01**: C++17 simulator compiles clean with Makefile — existing
- ✓ **SIM-02**: LRU, FIFO, CLOCK, S3-FIFO, SIEVE implementations correct and benchmarked — existing
- ✓ **SIM-03**: Zipf trace generation with per-object sizes — existing
- ✓ **SIM-04**: MLE Zipf alpha estimator (Clauset et al.) — existing
- ✓ **SIM-05**: One-hit-wonder ratio analysis — existing
- ✓ **SIM-06**: SHARDS MRC at 0.1%/1%/10% sampling — existing
- ✓ **SIM-07**: Exact stack distance validation against SHARDS — existing
- ✓ **SIM-08**: Alpha sensitivity sweep — existing
- ✓ **TRACE-01**: Congress.gov trace collector with rate-limiting + backoff — existing
- ✓ **TRACE-02**: Replay-Zipf mode overlays Zipf popularity on real keys/sizes — existing
- ✓ **PLOT-01**: matplotlib pipeline produces 6 figures (MRC, byte MRC, alpha, OHW, SHARDS MRC, workload) — existing
- ✓ **DOC-01**: Progress report, PROCESS.md, README, codebase map — existing

### Active (new work for submission)

**Second trace source (PACER/CourtListener):**

- [x] **TRACE-03**: Research PACER vs CourtListener/RECAP access — pick viable source (validated Phase 1: CourtListener REST v4 picked; token configured; live-API verified)
- [x] **TRACE-04**: Implement court-records trace collector with rate-limiting (validated Phase 1 as pilot: `scripts/pilot_court_trace.py` — 200-req pilot ALL PASS on 4 endpoints)
- [ ] **TRACE-05**: Collect a ≥20K-request court records trace
- [ ] **TRACE-06**: Run all policies on court records via replay-Zipf; compare to Congress

**W-TinyLFU policy:**

- [ ] **SIM-09**: Implement Count-Min Sketch frequency counter (frequency sketch)
- [ ] **SIM-10**: Implement W-TinyLFU policy (1% window LRU + 99% main with TinyLFU admission + Doorkeeper Bloom filter)
- [ ] **SIM-11**: Add W-TinyLFU to the policy comparison in main.cpp and all sweeps

**SHARDS rigor:**

- [x] **SIM-12**: Generate a ≥1M-access synthetic trace for SHARDS validation (validated Phase 4 Plan 01 as SHARDS-01; traces/shards_large.csv 1,000,001 lines, seed=42)
- [x] **SIM-13**: Report SHARDS error at 0.01%/0.1%/1%/10% on the large trace (validated Phase 4 Plan 01 as SHARDS-02/03; self-convergence via 3-row shards_convergence.csv since no exact oracle is feasible at 1M; MAE(1%, 10%) = 0.0378 passes sanity gate; 50K oracle regime cross-check at shards_error.csv — paper's 0.1% MAE target context-dependent and reported alongside the self-convergence MAE table)

**Analysis & writeup:**

- [x] **ANAL-01**: Cross-workload comparison: Congress vs court records (same 6 policies, 2 traces) — validated Phase 5 Plans 05-04+05-05: scripts/compare_workloads.py aggregation pipeline + 4 cross-workload figures (compare_mrc_2panel with ±1σ bands = canonical DOC-02 figure, compare_policy_delta, compare_mrc_overlay, winner_per_regime_bar) produced and regeneration verified
- [x] **ANAL-02**: Discuss when each policy wins (skew, size distribution, OHW regime) — validated Phase 5 Plans 05-03+05-04: 20 per-seed CSVs (5 seeds × 2 workloads × 2 stems) + Welch's t-test via scipy.stats.ttest_ind(equal_var=False) flagging n.s. at p≥0.05; W-TinyLFU dominance over LRU on Congress formally confirmed with p∈[1.2e-06, 8.7e-08] at every α in {0.6..1.2}
- [ ] **DOC-02**: Final report (flexible length, class-report style)
- [ ] **DOC-03**: AI-use report documenting what worked/didn't with Claude Code (professor request)
- [ ] **DOC-04**: Live-demo script for presentation (simulator runs with parameter sweeps)

### Out of Scope

- Production-grade caching system or real storage integration — this is a simulator
- Tracking real server-side traffic — we only replay synthetic popularity over real object metadata; Congress.gov and PACER don't publish access logs
- Multi-tier caching / distributed caches — single-tier object cache only
- Cost-based eviction (GDSF, LFUDA, etc.) beyond W-TinyLFU — scope containment
- Threading / concurrent cache access — simulator is single-threaded by design
- A third trace source — two domains are enough for the cross-workload story

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| PACER (vs OpenSecrets, SEC EDGAR) as second trace | Stays in public-records legal-adjacent domain; contrasting size distribution (legal documents tend to be larger than bill JSON); richer endpoint taxonomy | ✓ Validated Phase 1 — CourtListener REST v4 chosen (not PACER direct); 4 endpoint families non-gated for authenticated account |
| W-TinyLFU (vs just admission filter or basic TinyLFU) | Most impressive of the three options; matches what Caffeine ships; adding a frequency sketch is good systems-course material | — Pending |
| Larger SHARDS validation trace (synthetic, not real) | Real traces are rate-limited; synthetic gives enough scale (1M+ accesses) to meaningfully test 0.1% and 0.01% sampling | — Pending |
| Live simulator demo | More engaging than static slides; shows the system working; parameter sweeps let us illustrate findings in real time | — Pending |
| Replay-Zipf kept as primary analysis approach | Raw traces have near-zero temporal locality because they're client-generated random queries; replay-Zipf uses real keys/sizes with controlled popularity, which is what actually makes policy comparison meaningful | ✓ Validated in existing work; further confirmed Phase 5: raw Congress trace α_mle=0.231 (near-uniform), near-zero OHW (0.989 = 98.9% one-hit-wonders), so replay-Zipf's controlled α sweep is the only way to get a meaningful policy comparison |
| 5-seed CI over single-seed runs for final comparison | Single-seed runs can't distinguish real policy differences from RNG noise. 5 seeds is the smallest set that makes Welch's t-test credible at p<0.05 without over-investing in sweep wall-clock. | ✓ Validated Phase 5 Plans 05-03/05-04: 5 seeds × 2 workloads × 2 sweeps = 10 cache_sim invocations in 58.2s wall-clock (30× under budget); ±1σ bands visually separate policies on the canonical 2-panel DOC-02 figure |
| BASE_POLICIES restriction on regime analysis (exclude ablation variants) | D-01 regimes define the *main story* — 6 base policies compared. Ablations (S3-FIFO-5/20, SIEVE-NoProm, W-TinyLFU+DK) have their own Phase 4 figures and would muddy the regime-winner table. | ✓ Validated Phase 5 Plan 05-06: BASE_POLICIES=["LRU","FIFO","CLOCK","S3-FIFO","SIEVE","W-TinyLFU"] filter applied in _winner_in_group; zero ablation contamination in winner_per_regime.{md,json} |

## Risks

- **PACER trace collection is the highest-risk unknown.** Need to research access (PACER fees vs RECAP/CourtListener free tier), rate limits, and whether the endpoint structure supports the same replay-Zipf approach as Congress. Trace collection took a few hours for Congress (20K at ~1.6s/request); court records might be slower or require different throttling. This risk should be addressed early in the roadmap.
- **W-TinyLFU correctness.** Count-Min Sketch + Bloom filter + two cache regions is the most complex policy by far. Plan to validate against published W-TinyLFU results on known workloads before trusting our numbers.
- **Cross-workload story quality.** The writeup needs to say something interesting about *why* policies differ (or don't) across Congress vs PACER. If the two traces produce similar policy rankings, the writeup is duller. Mitigation: if the numbers are similar, the *similarity* itself is a finding (policies robust across legal public-records workloads).
- **Scope creep.** 4+ weeks runway is comfortable but not infinite. Budget time for report writing (often underestimated) and demo prep.

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-04-21 after Phase 5 completion (ANAL-01..04 verified; cross-workload analysis infrastructure complete; 5-seed Welch's t-test + ±1σ CI bands + workload characterization table + winner-per-regime analysis all landed under results/compare/)*
