# Phase 5: Cross-Workload Analysis Infrastructure - Context

**Gathered:** 2026-04-20
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 5 delivers the **analysis artifacts DOC-02 (Phase 6) is built from** — cross-workload comparison plots, multi-seed confidence intervals, a workload-characterization table, and a winner-per-regime table/figure. All four are numbers-and-plots deliverables, not new simulator capabilities.

**In scope:**
- `scripts/compare_workloads.py` (ANAL-01) — reads both result trees, emits comparison plots + summary tables in `results/compare/`
- 5-seed multi-run final sweep on both traces with Welch's t-test for `n.s.` flagging (ANAL-02)
- Workload-characterization table (ANAL-03) — Congress vs Court side-by-side using `workload_stats_json.py` output
- Winner-per-regime table/figure (ANAL-04) — 4 regimes × 2 workloads

**Micro C++ exception:** a single `--seed N` CLI flag on `cache_sim` (threaded into `generate_zipf_trace` + SHARDS sampler) is the only C++ edit. Everything else is Python scripts and data products.

**Out of scope (defer to Phase 6):**
- The final paper text (DOC-02) — uses the outputs but isn't produced here
- The practitioner decision-tree narrative — needs the winner-per-regime data, but the prose structure belongs to DOC-02
- New policies, new workloads, new ablation dimensions
- Third-trace validation (V2-03 SEC EDGAR — deferred to v2)

</domain>

<prior_decisions>
## Locked decisions from prior phases

- **D-01 (Phase 4):** 200-sample SHARDS floor — applies if compare plots overlay SHARDS error
- **D-15 (Phase 3):** `traces/` committed, `results/` gitignored — Phase 5 outputs under `results/compare/` follow the same convention; committed figures/tables land in `.planning/` artifacts or get emitted into the writeup at Phase 6
- **L-12 (Phase 2):** stats single-source — applies only if Phase 5 scripts recompute record(true|false) counts. They won't; all analysis is post-hoc over CSVs.
- **D-14 (Phase 1):** `std::hash` banned — applies to any new C++ `--seed` wiring; use `hash_util.h` machinery if seeded PRNGs are added
- **FNV-1a seeds:** 4 deterministic seeds in `include/hash_util.h` (not used for multi-seed; see D-S4 below)
- **POLICY_COLORS / POLICY_MARKERS** (Phases 2/4): established for all 6 base policies + 3 S3-FIFO variants + SIEVE-NoProm + W-TinyLFU+DK. Phase 5 plots REUSE these, never re-pick.
- **`check_wtlfu_acceptance.py` pattern (Phase 2):** grep-discoverable constants + exit-gate acceptance script. Phase 5 acceptance script follows this pattern if one is added.
- **Makefile per-workload pattern (Phase 3):** `make run-sweep WORKLOAD=court TRACE=traces/court_trace.csv`. Phase 5 multi-seed wrapper should respect this idiom.

</prior_decisions>

<existing_assets>
## Reusable assets (from scout)

**Data — all raw CSVs already exist on both workloads:**
- `results/{congress,court}/mrc.csv` — 6 policies × 6 cache_frac {0.001, 0.005, 0.01, 0.02, 0.05, 0.1}
- `results/{congress,court}/alpha_sensitivity.csv` — 6 policies × 7 α {0.6..1.2}
- `results/{congress,court}/ablation_{s3fifo,sieve,doorkeeper}.csv` (from Phase 4)
- `results/{congress,court}/one_hit_wonder.csv` — OHW at multiple window lengths
- `results/court/workload_stats.json` exists; `results/congress/workload_stats.json` DOES NOT (gap to close)

**Scripts:**
- `scripts/workload_stats_json.py` — emits the exact keys ANAL-03 table needs (trace_path, total_requests, unique_objects, alpha_mle, ohw_ratio, mean_size, median_size, p95_size, max_size, working_set_bytes)
- `scripts/check_wtlfu_acceptance.py` — acceptance-gate template
- `scripts/plot_results.py` — 6+ plot functions with the locked POLICY_COLORS/MARKERS convention

**Directory:** `results/compare/` already exists (reserved Phase 1 REFACTOR-04).

</existing_assets>

<decisions>
## Decisions — Phase 5

### D-01: Regime definitions for ANAL-04 winner-per-regime

| Regime | Definition | Cells in table |
|--------|------------|----------------|
| Small cache | `cache_frac = 0.001` only (smallest in existing mrc.csv grid) | 1 row × 2 workloads × 6 policies = 12 cells |
| High skew | α ∈ {1.0, 1.1, 1.2} (from alpha_sensitivity.csv) | 3 rows × 2 workloads × 6 policies = 36 cells |
| Mixed sizes | Byte-MRC (`byte_miss_ratio` column) on Court only — Court is the empirically heavy-tailed workload (median 1381 B, max 462 KB per Phase 3); Congress is "uniform sizes" by contrast. No arbitrary threshold. | 6 rows × 1 workload × 6 policies = 36 cells |
| OHW regime | Empirical from `workload_stats.json`: whichever workload has the higher OHW ratio becomes the "high-OHW regime"; the other is "low-OHW". Winner per regime is the policy with lowest miss_ratio in that workload at a reference cache_frac (the canonical 1% cell). | 2 rows × 6 policies = 12 cells |

**Why:** every definition either reuses an existing grid cell or a grid column — no new simulator work, no arbitrary thresholds for the paper to defend.

### D-02: Statistical test for n.s. flagging

- **Test:** Welch's unequal-variance t-test via `scipy.stats.ttest_ind(equal_var=False)` on the 5-seed samples for policy A vs policy B. Flag `n.s.` in output tables if p ≥ 0.05.
- **Bundling:** per-cell output includes mean, std, n=5, p-value (if compared), and `significant` boolean.
- **Rationale:** stronger rigor claim than literal 2σ; "significant at p<0.05" is standard class-report paper language; scipy.stats is already reachable in the existing Python env.

### D-03: CI bands on plots

- **Band shape:** `mean ± 1σ` via `matplotlib.axes.Axes.fill_between(mean-std, mean+std, alpha=0.2)`.
- **Rationale:** literal to ROADMAP SC-2 ("mean ± std-dev bands"). t-test lives in the tables; plots stay eyeball-friendly.

### D-04: Cross-workload plot catalogue

Ship four plot functions in `results/compare/figures/`:

1. **`compare_mrc_2panel.pdf`** — side-by-side (Congress | Court), all 6 policies, miss_ratio vs cache_frac, with ±1σ CI bands (this is the **canonical DOC-02 figure**).
2. **`compare_policy_delta.pdf`** — one line per policy, x-axis = cache_frac (or α), y-axis = Court − Congress miss_ratio. Directly answers "does this policy generalize?"
3. **`compare_mrc_overlay.pdf`** — single panel, solid = Congress, dashed = Court, 12 lines. Denser view for the workload-invariance discussion.
4. **`winner_per_regime_bar.pdf`** — grouped bar chart, 4 regimes × 2 workloads, winning policy highlighted per group. Paired with the markdown table for DOC-02's decision-tree section.

Alpha-sensitivity version of #1 is nice-to-have but not in Phase 5's critical path — mention in deferred-ideas if time.

### D-05: Multi-seed coverage

- **Coverage:** full grid — 6 policies × (6 cache_frac MRC + 7 α sensitivity) × 2 workloads × 5 seeds = 780 sim cells
- **Seeds:** `{42, 7, 13, 23, 31}` (first prime starting from existing default 42, plus four small primes)
- **Wall-clock budget:** planner should estimate 10–20 min on the target laptop; if runtime blows past 30 min, wave 2 of Phase 5 may need `--limit N` runs for faster iteration while final paper numbers come from the full grid.
- **Rationale:** full coverage lets every figure/table claim be "± 1σ across 5 seeds", which is a cleaner paper story than "CIs on some cells, point estimates on others".

### D-06: Seed plumbing (the micro C++ exception)

- **Mechanism:** new `--seed N` CLI flag on `cache_sim` (argparse entry in `src/main.cpp`).
- **Scope:** the seed threads into `generate_zipf_trace` (Zipf sampler) AND the SHARDS sampler (`src/shards.cpp`). It does NOT change the deterministic `seed=42` fallback path (Phase 4 Plan 01's `--emit-trace` hardcoded seed=42 — keep that literal for traces/shards_large.csv provenance).
- **Back-compat:** absent `--seed` must reproduce Phase 1–4 results byte-for-byte. Plan this with a Phase 4-style back-compat guard (default value preserves the old code path).
- **Python wrapper:** `scripts/run_multiseed_sweep.py` (new) orchestrates the 5 seed calls per (policy, workload) cell and emits per-seed CSVs that are then aggregated by compare_workloads.py.

### D-07: Missing Congress workload_stats.json

- Phase 3 only ran `workload_stats_json.py` against Court. Phase 5 must regenerate Congress's stats as a Wave 1 task before ANAL-03's table can be built.
- Output target: `results/congress/workload_stats.json` (gitignored per D-15).
- Invocation: `python3 scripts/workload_stats_json.py --trace traces/congress_trace.csv --output results/congress/workload_stats.json` (verify the trace filename — Phase 1 convention).

### D-08: Output formats for ANAL-03 / ANAL-04 tables

- **Primary:** markdown (easy paste into DOC-02; renders in Github + VSCode).
- **Secondary:** also emit JSON for machine-readable regression-gate use (parallel to `workload_stats.json` precedent).
- **LaTeX:** not in Phase 5 scope — if DOC-02 uses LaTeX, a one-liner pandoc conversion at Phase 6 suffices. Deferred.

### D-09: results/compare/ output file layout

```
results/compare/
├── workload_characterization.md   (ANAL-03 markdown table)
├── workload_characterization.json (ANAL-03 machine-readable)
├── winner_per_regime.md           (ANAL-04 markdown table)
├── winner_per_regime.json         (ANAL-04 machine-readable)
├── multiseed/                     (per-seed raw CSVs, seed-sweep outputs)
│   ├── congress/{mrc,alpha_sensitivity}_seed{42,7,13,23,31}.csv
│   └── court/{mrc,alpha_sensitivity}_seed{42,7,13,23,31}.csv
├── aggregated/                    (ANAL-02 aggregated CSVs with mean, std, p_value, significant)
│   ├── congress/{mrc,alpha_sensitivity}_aggregated.csv
│   └── court/{mrc,alpha_sensitivity}_aggregated.csv
└── figures/
    ├── compare_mrc_2panel.pdf      (canonical DOC-02 figure)
    ├── compare_policy_delta.pdf
    ├── compare_mrc_overlay.pdf
    └── winner_per_regime_bar.pdf
```

All gitignored per D-15. Figures committed to `.planning/phases/05-cross-workload-analysis-infrastructure/` at phase completion if the project wants archival persistence.

### D-10: Acceptance gate script

Follow the `check_wtlfu_acceptance.py` pattern (Phase 2): a `scripts/check_anal_acceptance.py` with grep-discoverable constants that asserts the 4 ROADMAP SC conditions are structurally met:
1. `scripts/compare_workloads.py` exists and produces `results/compare/{figures/,*.md,*.json}` outputs
2. All 5 seed files exist under `results/compare/multiseed/{congress,court}/`
3. `workload_characterization.md` has both workloads' rows populated (no missing cells)
4. `winner_per_regime.md` has all 4 regimes represented with at least 1 winner per regime

Exit 0 = all four SC structurally verified; exit 1 with a specific diagnostic otherwise. Same single-file-single-assertion-list pattern as Phase 2's acceptance script.

</decisions>

<specifics>
## User specifics

- **Regimes chosen tight:** small=0.1% only (not ≤1%), high-skew α≥1.0 (not ≥0.8), mixed-sizes via empirical workload distinction. Pattern is "let the workloads define the regimes" — minimizes arbitrary thresholds to defend in the paper.
- **Stats rigor upgrade:** Welch's t-test for the table, ±1σ for the plots — formal where formal matters (the `n.s.` column in DOC-02's comparison table is what reviewers will scrutinize), visual where visual matters.
- **Four plot views shipped:** user wanted all of 2-panel + Δ + overlay + bar — this is more figures than a typical class report uses, but each view answers a different question in DOC-02. If the page budget binds in Phase 6, #4 (bar chart) is the most likely omission since its story is also in the markdown table.

</specifics>

<canonical_refs>
## Canonical refs (MUST read before planning)

- `.planning/ROADMAP.md` — Phase 5 goal + SC-1 through SC-4 (lines 92-101)
- `.planning/REQUIREMENTS.md` — ANAL-01..04 (lines 74-78), traceability table
- `.planning/PROJECT.md` — core value; vision for "defensible, well-analyzed comparison"
- `.planning/codebase/CONVENTIONS.md` — Python script conventions, pytest absence, style expectations
- `.planning/codebase/STACK.md` — dependency policy; Python stdlib + matplotlib + scipy (already pulled via matplotlib env)
- `scripts/workload_stats_json.py` — existing implementation, output-key contract for ANAL-03
- `scripts/check_wtlfu_acceptance.py` — Phase 2 acceptance-gate precedent for `check_anal_acceptance.py`
- `scripts/plot_results.py` — POLICY_COLORS / POLICY_MARKERS / 2-panel plot pattern
- `results/court/workload_stats.json` — Phase 3 reference output; Congress regeneration must match this schema
- `results/{congress,court}/ablation_*.csv` — Phase 4 raw data (not directly used by Phase 5 unless DOC-02 cross-references ablation findings)
- `src/main.cpp` — argparse block to extend with `--seed N`, generate_zipf_trace + SHARDS call sites to thread seed into
- `include/hash_util.h` — FNV seed infrastructure (reference, not directly used for multi-seed)

</canonical_refs>

<deferred_ideas>
## Deferred ideas (not in Phase 5)

- **Alpha-sensitivity CI figure (alpha-version of canonical MRC 2-panel)** — nice-to-have; skip unless planner finds it trivial to add alongside MRC CI plot
- **LaTeX table emission** — one-liner pandoc at Phase 6; not Phase 5 work
- **Third trace source (SEC EDGAR)** — deferred to v2 (V2-03 in REQUIREMENTS)
- **LHD / AdaptSize as 7th policy** — deferred to v2 (V2-02)
- **Caffeine Java trace cross-validation** — deferred to v2 (V2-01)
- **Practitioner decision tree narrative** — belongs in DOC-02 (Phase 6), needs ANAL-04's winner table as input
- **Regression-test gate in CI** — no CI wired; `check_anal_acceptance.py` is a local manual gate. A CI hook is a separate project-hygiene task outside Phase 5's scope.

</deferred_ideas>
