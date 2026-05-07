# Phase 4: SHARDS Large-Scale Validation & Ablations — Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-20
**Phase:** 04-shards-large-scale-validation-ablations
**Areas discussed:** SHARDS sampling strategy, Doorkeeper API + integration, Ablation CLI + parameterization, 1M trace + output layout

---

## SHARDS sampling strategy

### Q1: Rate-floor handling for 0.01% on 1M trace

At 1M accesses × 0.01% = 100 sampled accesses, below PITFALLS M4's ≥200-sample recommendation.

| Option | Description | Selected |
|--------|-------------|----------|
| Keep 0.01% with flagged caveat | Report all 4 rates; add `n_samples` column; writeup + figure flag 0.01% as "below paper-recommended 200-sample floor". Matches Waldspurger FAST'15 table {0.0001, 0.001, 0.01, 0.1}. | ✓ |
| Drop 0.01%, report {0.1%, 1%, 10%} | Cleaner, all points pass. Loses lowest-rate data point. | |
| Upsize to 2M-access trace | 2M × 0.01% = 200 samples. Doubles SHARDS wall-clock at 10%; departs from ROADMAP's "1M". | |

**User's choice:** Keep 0.01% with flagged caveat (recommended)
**Notes:** Honest-to-paper-target framing; caveat is grep-discoverable in the CSV.

### Q2: Self-convergence MAE metric shape

| Option | Description | Selected |
|--------|-------------|----------|
| 10% as reference (three rows vs. 10%) | MAE(0.01% vs 10%), MAE(0.1% vs 10%), MAE(1% vs 10%). Monotone convergence table. Matches Waldspurger framing. | ✓ |
| Adjacent-pair MAE (chained) | Shows local convergence; no global reference; ⟨0.01%, 1%⟩ divergence invisible. | |
| Full pairwise matrix | 6-cell symmetric; most information, busier table. | |

**User's choice:** 10% as reference (recommended)

### Q3: Parallel 50K oracle regime

| Option | Description | Selected |
|--------|-------------|----------|
| Yes — existing --shards-exact path at 50K | Free mitigation: oracle path caps at 50K (src/main.cpp:334). Two regimes: "oracle MAE at 50K" + "self-convergence MAE at 1M". Directly answers PITFALLS M4. | ✓ |
| No — 1M self-convergence only | Simpler. "How do we know SHARDS is correct at 1M?" hand-waved. | |
| Yes — defer to Phase 6 writeup | Keeps Phase 4 scope tighter but defers the mitigation. | |

**User's choice:** Yes — use existing --shards-exact path at 50K (recommended)

---

## Doorkeeper API + integration

### Q4: Where does the Doorkeeper sit?

| Option | Description | Selected |
|--------|-------------|----------|
| Pre-CMS record filter (paper-faithful) | First touch: doorkeeper.add + SKIP cms.record. Second touch: cms.record. Matches Einziger-Friedman §4.3. Paper's "50-70% CMS-pressure reduction" claim defensible. | ✓ |
| Admission-gate short-circuit | CMS records all; DK gates admission decision. ARCHITECTURE.md Pattern 3 snippet; muddles what DK is actually filtering. | |
| Both (two separate ablations) | Richest data; triples surface area for a secondary ablation. | |

**User's choice:** Pre-CMS record filter (recommended)
**Notes:** Paper-faithful; explicitly overrides ARCHITECTURE.md Pattern 3 snippet.

### Q5: Variant selection/naming

| Option | Description | Selected |
|--------|-------------|----------|
| New policy string `wtinylfu-dk` | Ctor flag `use_doorkeeper=false` default; one make_policy branch; one plot entry. Phase 2 pattern. | ✓ |
| Separate class `WTinyLFUDoorkeeperCache` | New header; duplicates access() body or needs protected hooks. More surface area for L-12 to leak. | |
| CLI flag `--doorkeeper` global toggle | Can't run baseline + DK in same sweep; ablation figure needs both. | |

**User's choice:** New policy string `wtinylfu-dk` (recommended)

### Q6: DK reset sync with CMS aging

| Option | Description | Selected |
|--------|-------------|----------|
| CMS on_age() callback, WTinyLFU wires DK.clear() | Minimal CMS hook; WTinyLFU registers lambda when DK on. Same cadence (10×W); no extra counter. Respects L-12. | ✓ |
| DK self-manages own reset counter | Two aging cadences drift; exactly what STACK.md says NOT to do. | |
| Halve instead of clear (4-bit style) | DK is 1-bit; halving zeros everything. Don't overthink; clear() is the API. | |

**User's choice:** CMS exposes on_age() callback (recommended)

---

## Ablation CLI + parameterization

### Q7: S3-FIFO small-queue ratio parameterization

| Option | Description | Selected |
|--------|-------------|----------|
| Ctor param + three policy strings | `S3FIFOCache(cap, small_frac=0.1)`; `s3fifo-5/-10/-20` branches. Single-invocation sweep. | ✓ |
| Single policy + --s3fifo-small-frac CLI flag | Can't sweep ratios in single invocation. | |
| Separate shell-script driver | Scatters ablation logic across two languages. | |

**User's choice:** Ctor param + three policy strings (recommended)

### Q8: SIEVE visited-bit ablation

| Option | Description | Selected |
|--------|-------------|----------|
| Ctor flag + `sieve-noprom` policy string | `SIEVECache(cap, bool promote_on_hit=true)`; guards the visited=true line. Symmetric with S3-FIFO. | ✓ |
| Separate class `SIEVENoPromoteCache` | 50 lines duplicated code. | |
| Global --sieve-no-promote flag | Same downside as S3-FIFO global flag. | |

**User's choice:** Ctor flag + `sieve-noprom` policy string (recommended)

### Q9: Fixed cache size for ablations

| Option | Description | Selected |
|--------|-------------|----------|
| 1% of working set | Matches Phase 2 alpha-sensitivity convention (wb/100). | ✓ |
| Full cache-fraction grid {0.1%..10%} | Full curve per ablation; 6× wall-clock; PITFALLS M3 rationale. | |
| 3 cache sizes {0.5%, 1%, 5%} | Compromise. | |

**User's choice:** 1% of working set (recommended)
**Notes:** Each ablation still runs across the full alpha grid — the "fixed cache" is on the cache-size axis, not the skew axis.

---

## 1M trace + output layout

### Q10: 1M trace path + commit policy

| Option | Description | Selected |
|--------|-------------|----------|
| `traces/shards_large.csv` gitignored | Phase 1 D-04 convention; 45MB not in git; <10s regen; seed=42 deterministic. | ✓ |
| `traces/shards_large.csv` committed | Free for grader/demo; 45MB repo bloat. | |
| `results/shards_large/trace.csv` gitignored | Breaks Phase 1 input/output separation. | |

**User's choice:** `traces/shards_large.csv` gitignored, regen on-demand (recommended)

### Q11: Output artifacts + CSV schema

| Option | Description | Selected |
|--------|-------------|----------|
| Extend existing schemas + add convergence CSV | Phase 1 REFACTOR-03 schemas unchanged; new `shards_convergence.csv`; flat layout. | ✓ |
| One merged `shards_1m.csv` | Wide table; plot_results.py readers assume separate schemas. | |
| Separate subdirs under shards_large/ | Over-structured for 3-4 CSVs. | |

**User's choice:** Extend existing schemas; add convergence CSV (recommended)

### Q12: Makefile invocation

| Option | Description | Selected |
|--------|-------------|----------|
| New dedicated target `make shards-large` | Ablations get own targets too; independent of Phase 3 WORKLOAD plumbing; one src/main.cpp change (`--shards-rates` flag). | ✓ |
| Reuse `make run-sweep WORKLOAD=shards_large` | Contorts the Phase 3 abstraction; synthetic trace isn't a workload. | |
| Megatarget `make phase-04` | Composition; fine on top of dedicated targets, not replacement. | |

**User's choice:** New dedicated target `make shards-large` (recommended)
**Notes:** Ablation targets follow same pattern: `ablation-s3fifo`, `ablation-sieve`, `ablation-doorkeeper`.

---

## Claude's Discretion

- Concrete color/marker styling for the 4 new policy-string variants in plot_results.py
- Exact number of MRC grid points (100 is existing default)
- Whether the trace generator is Python or a C++ `--emit-trace` flag — planner's call
- Ordering of plots within figures (workload left/right, rate ascending)
- Error-bar vs. band styling on DK-ablation figure
- Whether a composition `make phase-04` target exists

## Deferred Ideas

- Balanced-BST O(n log n) exact oracle (replacing O(n²)) — v2 or Phase 6 enrichment
- Doorkeeper at admission-gate as alternative ablation — v2 if reviewer asks
- Multi-seed ablation variance bands — Phase 5 (ANAL-02)
- `--shards-rates` beyond 10% (25%, 50%) — overkill
- `scripts/check_phase4_acceptance.py` — nice-to-have, non-blocking
- Committing `traces/shards_large.csv` — revisit only if demo regen >30s
- Caffeine Doorkeeper cross-validation — V2-01 (already deferred)
- Doorkeeper FPR tuning study — overkill
