# Phase 4: SHARDS Large-Scale Validation & Ablations — Context

**Gathered:** 2026-04-20
**Status:** Ready for planning

<domain>
## Phase Boundary

Defensible rigor claims for the final writeup — three independent axes:

1. **SHARDS self-convergence at 1M scale** across 4 sampling rates {0.01%, 0.1%, 1%, 10%}, with a parallel 50K sub-trace oracle regime so the writeup has both "self-consistency at 1M" and "vs. exact at 50K" numbers (PITFALLS M4 two-regime mitigation).
2. **Doorkeeper Bloom filter as an ablation** — a sibling `wtinylfu-dk` policy variant (baseline `wtinylfu` stays Doorkeeper-free per Phase 2 and Caffeine precedent), positioned as a paper-faithful pre-CMS first-touch filter.
3. **Two parameter-sensitivity ablations** — S3-FIFO small-queue ratio sweep (5%/10%/20%) and SIEVE visited-bit on/off, both run on Congress AND Court at 1% of working set.

What's explicitly IN scope:

1. `traces/shards_large.csv` — 1M-access synthetic Zipf(α=0.8, 100K objects, seed=42), gitignored, regenerated on-demand (<10s). One `scripts/generate_shards_trace.py` or equivalent C++ flag.
2. `src/main.cpp::--shards-rates` CLI flag — replaces the hardcoded `{0.001, 0.01, 0.1}` at main.cpp:304, defaults preserved (back-compat with Phase 1 Congress runs).
3. `include/doorkeeper.h` — header-only Bloom filter: configurable bit-array size, 2 hash functions via Kirsch-Mitzenmacher double-hashing (`h1 + i·h2`), `contains()`, `add()`, `clear()`.
4. `include/wtinylfu.h` extension — one ctor flag `use_doorkeeper=false` (default preserves Phase 2 behavior); internal `Doorkeeper doorkeeper_` member only instantiated when flag is true; pre-CMS record-filter integration in `access()`.
5. `include/count_min_sketch.h` extension — minimal `on_age` callback hook fired from `halve_all_()`; WTinyLFUCache registers `[this]{ doorkeeper_.clear(); }` when DK is on.
6. `include/cache.h` extensions — `S3FIFOCache` ctor gains `double small_frac=0.1` (default preserves Phase 1); `SIEVECache` ctor gains `bool promote_on_hit=true` (default preserves Phase 1).
7. `src/main.cpp::make_policy` branches — new policy strings `wtinylfu-dk`, `s3fifo-5`, `s3fifo-10`, `s3fifo-20`, `sieve-noprom`; legacy `s3fifo` and `sieve` preserved as aliases of `s3fifo-10` and promote-on-hit respectively.
8. `results/shards_large/{shards_mrc.csv, shards_convergence.csv, shards_error.csv, figures/*.pdf}` — convergence CSV schema: `reference_rate, compared_rate, mae, max_abs_error, num_points, n_samples_reference, n_samples_compared`.
9. `results/{congress,court}/{ablation_s3fifo.csv, ablation_sieve.csv, ablation_doorkeeper.csv}` — per-workload ablation outputs.
10. `scripts/plot_results.py` extensions — 4 new plot functions registered in `main()`: SHARDS convergence (error-vs-rate line + MRC overlay), Doorkeeper ablation (2×2 grid workload×variant), S3-FIFO ratio, SIEVE visited-bit.
11. `Makefile` targets — new: `shards-large`, `ablation-s3fifo`, `ablation-sieve`, `ablation-doorkeeper`. Independent of `make run-sweep`; the Phase 3 `WORKLOAD=`/`TRACE=` plumbing is not reused for these.
12. `tests/test_doorkeeper.cpp` — Bloom filter unit tests (contains-after-add, clear zeros all, FPR sanity at expected load). Follows Phase 2 D-04..D-07 pattern (pure C++17 assert, separate `build/test/` dir, `make test` target extended).

What's explicitly NOT in scope:

- Cross-workload comparison plots (Congress vs. Court side-by-side) — Phase 5 (ANAL-01, ANAL-03)
- Multi-seed confidence-interval runs — Phase 5 (ANAL-02)
- SHARDS on Congress or Court real traces — Phase 4 uses the 1M synthetic only (real traces at 20K don't stress the 0.01% rate)
- W-TinyLFU hill-climbing window tuner — permanently omitted (Phase 2 L-8)
- A balanced-BST exact-stack-distance oracle replacing the current O(n²) path — deferred; the 50K sub-slice is enough for the oracle regime
- Doorkeeper as part of baseline `wtinylfu` — it stays a sibling variant only (Caffeine precedent + PITFALLS C6)

</domain>

<decisions>
## Implementation Decisions

### SHARDS Sampling Strategy

- **D-01:** **Keep the 0.01% sampling rate with a flagged caveat.** At 1M accesses × 0.01% = 100 sampled accesses, below PITFALLS M4's ≥200 recommendation. Report all 4 rates {0.01%, 0.1%, 1%, 10%}; add an `n_samples` column to `shards_convergence.csv` so the caveat is grep-visible; the writeup (Phase 6 DOC-02) + figure caption flag 0.01% as "below paper-recommended 200-sample floor" without dropping the data point. Matches Waldspurger FAST'15 table {0.0001, 0.001, 0.01, 0.1}.
- **D-02:** **Self-convergence MAE is reported with 10% as reference.** Three rows: MAE(0.01% vs 10%), MAE(0.1% vs 10%), MAE(1% vs 10%). Monotone convergence claim ("as sampling rate rises, MAE against 10% shrinks") is directly answerable from the table. Adjacent-pair MAE rejected: no global reference, and ⟨0.01%, 1%⟩ divergence is invisible in a chain. Full pairwise matrix rejected as writeup clutter.
- **D-03:** **Parallel 50K oracle regime via existing `--shards-exact` path.** On a 50K sub-slice of the 1M trace (first 50K rows, deterministic), run SHARDS {0.1%, 1%, 10%} alongside `exact_stack_distances` via the existing path at `src/main.cpp:334`. Output: `results/shards_large/shards_error.csv` (existing schema: `sampling_rate, mae, max_abs_error, num_points`). Writeup presents two regimes side-by-side: "oracle MAE at 50K" + "self-convergence MAE at 1M". The 0.01% rate is excluded from the 50K regime (5 samples — pointless).
- **D-04:** **Alignment strategy for MAE computation.** SHARDS at different rates produces MRCs on different cache-size grids because the scaling `estimated_distance = sampled_distance / rate` changes granularity. Use the existing `build_mrc(max_cache_size, num_points=100)` with `max_cache_size = unique_objects` (src/main.cpp:306) — this produces a uniform 100-point grid across all rates, making MAE computation pointwise without interpolation. Already the shape of `shards_error.csv` today.

### Doorkeeper API + Integration

- **D-05:** **Pre-CMS record filter (paper-faithful).** In `WTinyLFUCache::access()` when `use_doorkeeper_` is true: before `cms_.record(key)`, check `doorkeeper_.contains(key)`; if false, call `doorkeeper_.add(key)` and SKIP the CMS record (first-touch ignored by CMS). If true, call `cms_.record(key)` as today. This is the Einziger-Friedman §4.3 semantic — the paper's claim "Doorkeeper absorbs 50-70% of one-hit-wonder CMS pressure" is only defensible under pre-record filtering, not admission-gate short-circuit (which was the ARCHITECTURE.md Pattern 3 snippet). Admission-gate alternative rejected as muddling what DK is actually filtering.
- **D-06:** **Doorkeeper sizing.** Bit-array length = `4 × n_objects_hint` (4 bits per element, per STACK.md §Doorkeeper and Einziger-Friedman recommendation). `n_objects_hint` is the same value already threaded to the CMS via `make_policy(..., n_obj_hint)` in Phase 2 D-02. False-positive rate target ≈13% — adequate for first-time filtering. Ctor: `Doorkeeper(uint64_t n_objects_hint)` with internal `size_ = 4 * max(n_objects_hint, 1)`.
- **D-07:** **Doorkeeper hash scheme.** Kirsch-Mitzenmacher double-hashing with 2 FNV-1a seeds: `h1 = fnv1a_64(key, FNV_SEED_A)`, `h2 = fnv1a_64(key, FNV_SEED_B)`. For i in {0, 1}: `bit = (h1 + i * h2) % size_`. Reuses Phase 1's `hash_util.h` seeds; no new hash constants. Two hashes (not k>2) because the Doorkeeper's job is a first-time filter, not a precision hash set — 13% FPR is fine.
- **D-08:** **DK-variant selected via policy string `wtinylfu-dk`.** `WTinyLFUCache` ctor extended to `(uint64_t capacity_bytes, uint64_t n_objects_hint, bool use_doorkeeper = false)`. Default false preserves Phase 2 behavior exactly (no `doorkeeper_` member allocation, no behavior change, Phase 2's 4-grep `record(true|false)` invariant still holds). `make_policy` adds ONE branch: `if (name == "wtinylfu-dk") return make_unique<WTinyLFUCache>(cap, hint, /*use_doorkeeper=*/true)`. CLI: `--policies wtinylfu,wtinylfu-dk`.
- **D-09:** **CMS `on_age` callback wires DK reset.** Add minimal hook to `CountMinSketch`: `std::function<void()> on_age_cb_` member (default empty); fired from the end of `halve_all_()`. `WTinyLFUCache` ctor (when `use_doorkeeper=true`) registers a lambda capturing `this` that calls `doorkeeper_.clear()`. No new counter in Doorkeeper; no aging-cadence drift between DK and CMS (STACK.md §Doorkeeper: "aligned with the CMS so freshness is consistent"). When `use_doorkeeper=false`, callback stays empty and is not invoked — zero overhead for baseline wtinylfu. Stats single-source invariant (L-12) preserved: DK never touches `stats`.
- **D-10:** **Doorkeeper-on variant still uses the same CMS in admission.** The admission test (`admit_candidate_to_main_`, D-08a..D-08e from Phase 2) is unchanged. DK only gates what gets recorded into CMS; once a key has been recorded, admission uses `cms_.estimate()` as before. This keeps the variant's diff footprint minimal and makes the ablation interpretable ("same admission, different counting").

### Ablation CLI + Parameterization

- **D-11:** **S3-FIFO small-queue ratio as ctor param with three policy strings.** `S3FIFOCache` ctor extended to `(uint64_t capacity, double small_frac = 0.1)`; `small_capacity_ = max<uint64_t>(1, capacity * small_frac)`. `make_policy` adds branches: `s3fifo-5` (0.05), `s3fifo-10` (0.10), `s3fifo-20` (0.20). Existing `s3fifo` string remains an alias for `s3fifo-10`. Ablation sweep: `--policies s3fifo-5,s3fifo-10,s3fifo-20` in one invocation → single `ablation_s3fifo.csv`. All three variants get plot styling entries.
- **D-12:** **SIEVE visited-bit as ctor flag with policy string `sieve-noprom`.** `SIEVECache` ctor extended to `(uint64_t capacity, bool promote_on_hit = true)`; when false, the `it->second->visited = true` line in access() hit-path (cache.h:362) is guarded. `make_policy` adds `sieve-noprom` branch. Existing `sieve` string unchanged (promote_on_hit=true). Ablation sweep: `--policies sieve,sieve-noprom` → single `ablation_sieve.csv`.
- **D-13:** **Ablation sweeps fix cache at 1% of working set.** Matches Phase 2's alpha-sensitivity convention (`src/main.cpp:255` uses `wb/100`). ROADMAP says "at fixed cache size" — 1% is the convention. Full-grid rejected as 6× wall-clock for a secondary ablation; 3-size compromise rejected as under-answering ROADMAP's "fixed". Additionally: each ablation runs across the full alpha grid {0.6..1.2} so the figure shows parameter-sensitivity as a function of skew, not a single point.
- **D-14:** **Ablation sweeps run on BOTH workloads.** ROADMAP success criterion 4 is explicit: "both workloads". Implementation: `make ablation-s3fifo` and `make ablation-sieve` each invoke `cache_sim` twice — once with `--trace traces/congress_trace.csv --replay-zipf` and once with `traces/court_trace.csv --replay-zipf`. Output pairs: `results/congress/ablation_*.csv` + `results/court/ablation_*.csv`. DOOR-03 ablation figure uses the same two-workload pattern.

### 1M Trace + Output Layout

- **D-15:** **`traces/shards_large.csv` gitignored, regenerated on-demand.** Size estimate: 1M rows × ~45 bytes = ~45 MB. Matches Phase 1 D-04 convention (all `traces/*.csv` gitignored; Phase 3's `court_trace.csv` D-15 exemption does NOT apply — synthetic trace is deterministic under seed=42, no API-drift protection needed). Generation: `scripts/generate_shards_trace.py` (new, ~30 LoC, calls `generate_zipf_trace` equivalent in Python with numpy, OR prefer extending C++ `cache_sim` with a `--emit-trace <path>` flag). Runtime: <10s on dev laptop (Python) or <5s (C++). PITFALLS C7's "90s regeneration" risk is avoidable in both cases. `make shards-large` checks `test -f traces/shards_large.csv || <generate>` before running SHARDS.
- **D-16:** **Output artifacts in `results/shards_large/` — extend existing schemas, add convergence CSV.**
  - `shards_mrc.csv` — schema unchanged (Phase 1 REFACTOR-03): `sampling_rate, cache_size_objects, miss_ratio, accesses_per_sec`. Populated with all 4 rates × 100 grid points.
  - `shards_error.csv` — schema unchanged (Phase 1): `sampling_rate, mae, max_abs_error, num_points`. Populated only by the 50K oracle regime (D-03), not the 1M run. Rates included: {0.1%, 1%, 10%} only.
  - `shards_convergence.csv` — NEW schema: `reference_rate, compared_rate, mae, max_abs_error, num_points, n_samples_reference, n_samples_compared`. Three rows (all vs. 10%, per D-02). `n_samples_compared` carries the caveat flag for D-01 (100 samples for 0.01%).
  - `figures/shards_convergence.pdf` + `figures/shards_mrc_overlay.pdf` (PITFALLS M3 "overlay SHARDS approximation on exact MRC" — the money shot).
  - Flat layout, no subdirs (Phase 3 D-14 precedent).
- **D-17:** **Dedicated Makefile targets — independent of Phase 3's `WORKLOAD=`/`TRACE=` plumbing.** New targets: `shards-large`, `ablation-s3fifo`, `ablation-sieve`, `ablation-doorkeeper`, plus a composition convenience `phase-04` that runs them in sequence. Each target owns its invocation shape (SHARDS needs `--shards --shards-rates`, ablations need `--alpha-sweep --policies <list>`); reusing `run-sweep` would force a CLI-flag-juggling contortion via the WORKLOAD variable that's Phase 3's design. Phase 3's existing `run-sweep WORKLOAD=court` default behavior stays untouched.
- **D-18:** **One src/main.cpp change: `--shards-rates` CLI flag.** Today the SHARDS rate grid is hardcoded at main.cpp:304 (`{0.001, 0.01, 0.1}`). Replace with a parseable `--shards-rates 0.0001,0.001,0.01,0.1` flag with default value preserving current behavior (Phase 1 back-compat). Zero other simulator changes for Phase 4 SHARDS work. Parsing mirrors the existing `--cache-sizes` pattern (main.cpp:100-102, comma-split).

### Sequencing + Wave Parallelism

- **D-19:** **Four independent work axes — plan in parallel waves.** The three axes (SHARDS, Doorkeeper, Ablations) have no inter-dependencies:
  - **Axis A (SHARDS)**: `--shards-rates` flag + generate script + `make shards-large` + convergence CSV emission + 50K oracle regime + 2 plot functions. No header changes.
  - **Axis B (Doorkeeper)**: `include/doorkeeper.h` + CMS `on_age` hook + WTinyLFU ctor flag + `make_policy` branch + `tests/test_doorkeeper.cpp` + ablation figure + plot styling. Requires D-08/D-09 wiring.
  - **Axis C (S3-FIFO ablation)**: S3FIFOCache ctor param + 3 `make_policy` branches + 3 plot styling entries + ablation-s3fifo Makefile target + plot function.
  - **Axis D (SIEVE ablation)**: SIEVECache ctor flag + 1 `make_policy` branch + 1 plot styling entry + ablation-sieve Makefile target + plot function.
  Axes A/B/C/D can each become a wave (or sub-wave) in planning. Axis B's Doorkeeper ablation figure depends on both baseline wtinylfu (Phase 2, done) and the new wtinylfu-dk (this phase); no cross-axis dependency.

### Claude's Discretion

- Concrete `POLICY_COLORS` / `POLICY_MARKERS` values for `wtinylfu-dk`, `s3fifo-5`, `s3fifo-20`, `sieve-noprom` in `plot_results.py` — pick distinguishable-but-related colors (e.g., `wtinylfu` stays `#8c564b` brown solid, `wtinylfu-dk` brown dashed; `s3fifo-5`/`s3fifo-20` = lighter/darker red variants of `s3fifo-10`'s `#d62728`; `sieve-noprom` = purple dashed). Not worth a discussion cycle.
- Exact number of points in the SHARDS MRC grid — `num_points=100` (existing `build_mrc` default) is fine at 1M scale; no reason to deviate.
- Whether `scripts/generate_shards_trace.py` is Python or a C++ `--emit-trace` flag — either works; prefer whichever is shorter in the plan. Python dedup-scan of 1M rows is ~3s with numpy, C++ is ~1s; both below the 10s perceived-instant threshold.
- Order of plots within figures (rate ascending/descending, workload left/right) — follow existing `plot_mrc` convention (Congress left, rate ascending).
- Error-bar or band styling on the DK-ablation figure — same visual convention as Phase 2's alpha-sensitivity plot.
- Whether the `phase-04` composition Makefile target exists or the user always invokes `make shards-large ablation-s3fifo ablation-sieve ablation-doorkeeper` by hand — defer to planner; a 5-line composition target costs nothing.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents (researcher, planner) MUST read these before acting.**

### Requirements & roadmap
- `.planning/REQUIREMENTS.md` §SHARDS large-scale validation (SHARDS-xx), §Doorkeeper ablation (DOOR-xx), §Ablation studies (ABLA-xx) — 8 must-haves
- `.planning/ROADMAP.md` §Phase 4 — 4 success criteria
- `.planning/PROJECT.md` — "No external C++ deps" non-negotiable; SHARDS rigor rationale; Doorkeeper-as-ablation motivation

### Technical research (prior-work outputs — binding)
- `.planning/research/STACK.md` §Doorkeeper (lines 165-176) — bit budget 4×n_objects_hint, 2 hash functions, reset synchronized with CMS aging (drives D-06/D-07/D-09)
- `.planning/research/STACK.md` §W-TinyLFU Implementation Parameters (lines 150-185) — SLRU 80/20, 1%/99% window/main, admission test (Phase 2 baseline, not re-opened)
- `.planning/research/STACK.md` §Optional for the first cut (line 175) — DK kept as ablation, not baseline (drives D-08 ctor-flag pattern)
- `.planning/research/ARCHITECTURE.md` §191-195 — stats single-source invariant; Doorkeeper integration must NOT touch `stats` (L-12 from Phase 2)
- `.planning/research/ARCHITECTURE.md` Pattern 3 (lines 224-228) — `should_admit` method with DK short-circuit — EXPLICITLY REJECTED by D-05 in favor of pre-CMS record filter per paper
- `.planning/research/PITFALLS.md` §M4 (lines 179-190) — SHARDS sampling-rate-vs-accuracy table, ≥200-sample floor, 50K-subtrace oracle strategy (drives D-01/D-02/D-03)
- `.planning/research/PITFALLS.md` §M3 (lines 166-177) — MRC as full curve not discrete points; overlay SHARDS on exact MRC (drives D-16 figure plan)
- `.planning/research/PITFALLS.md` §C6 (lines 87-107) — Doorkeeper recommended OMITTED from baseline; drives the sibling-variant decision in D-08
- `.planning/research/PITFALLS.md` §C7 (lines 109-130) — demo-trace regeneration risk (drives D-15 gitignore + fast-regen decision)

### Phase 1 artifacts (prerequisites)
- `include/hash_util.h` — `fnv1a_64(key, seed)` with 4 seeds `FNV_SEED_A..D`; Doorkeeper uses A+B for its two hashes (D-07)
- `src/main.cpp:304` — current SHARDS rate hardcode `{0.001, 0.01, 0.1}` replaced by `--shards-rates` flag per D-18
- `src/main.cpp:334` — existing `--shards-exact` path capped at 50K; reused verbatim for D-03 oracle regime
- `.planning/phases/01-enabling-refactors-courtlistener-pilot/01-CONTEXT.md` — per-workload result subdirs (REFACTOR-04) + CSV schema conventions

### Phase 2 artifacts (prerequisites — Doorkeeper integrates into these)
- `include/wtinylfu.h` — WTinyLFUCache ctor signature extended per D-08; D-05 record-filter inserts before existing `cms_.record(key)` at line 60
- `include/count_min_sketch.h` — `on_age_cb_` hook added per D-09, fired from `halve_all_()` at line 128
- `.planning/phases/02-w-tinylfu-core/02-CONTEXT.md` — stats single-source invariant (L-12), Caffeine deviations, ctor signature precedent
- `.planning/phases/02-w-tinylfu-core/02-01-CAFFEINE-NOTES.md` — Caffeine omits Doorkeeper in production; our DK variant is ablation-only

### Phase 3 artifacts (prerequisites — ablations reuse these traces)
- `traces/court_trace.csv` — 20,001 rows (Phase 3 D-15 committed); Axis C+D ablations run replay-Zipf over it
- `traces/congress_trace.csv` — Milestone 1 trace; same
- `Makefile` `WORKLOAD=`/`TRACE=` parameterization — NOT reused for Phase 4 targets (D-17); Phase 4 adds dedicated targets alongside
- `.planning/phases/03-courtlistener-trace-collection-replay-sweep/03-CONTEXT.md` — output-layout precedent (D-14 flat subdirs)

### Existing cache.h surfaces to edit
- `include/cache.h:205-334` (S3FIFOCache) — ctor gains `double small_frac` per D-11; no other changes
- `include/cache.h:336-408` (SIEVECache) — ctor gains `bool promote_on_hit` per D-12; hit-path line 362 guarded by flag
- `src/main.cpp:62-71` (make_policy) — 4 new branches per D-08/D-11/D-12
- `src/main.cpp:184-190` + `:234-240` (policy label mapping) — add new display strings

### External sources
- Einziger, Friedman, Manes, "TinyLFU: A Highly Efficient Cache Admission Policy" (ACM TOS 2017) §4.3 Doorkeeper semantics — drives D-05 pre-CMS-record placement
- Waldspurger, Park, Garthwaite, Ahmad, "Efficient MRC Construction with SHARDS" (FAST 2015) — drives D-01 rate grid and D-02 MAE-table framing. Paper target: 0.1% MAE at 1% sampling on 1M-access traces.
- Yang, Rybczynski, Vigfusson, "S3-FIFO: A Simple, Scalable, FIFO-Based Algorithm for Efficient Caching" (SOSP 2023) — S3-FIFO 10% default justification (D-11 preserves as `s3fifo-10` baseline alias)
- Zhang, Yang, Song, Vigfusson, "SIEVE: A Simple, Scalable Eviction Algorithm" (NSDI 2024) — SIEVE visited-bit semantics (D-12 ablation turns off promotion on hit)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `include/hash_util.h` — `fnv1a_64(key, seed)` with 4 seeds; Doorkeeper uses A+B (D-07). No new hash work needed.
- `include/count_min_sketch.h` — has `width()`, `sample_size()`, `force_age()` inspector API + `halve_all_()` private method; minimal extension for `on_age_cb_` hook (D-09).
- `include/wtinylfu.h` — Phase 2 ctor `(capacity_bytes, n_objects_hint)`; extends to `(..., bool use_doorkeeper=false)` per D-08 preserving back-compat.
- `include/cache.h` (LRUCache/FIFOCache/CLOCKCache) — untouched; S3FIFOCache + SIEVECache get one-ctor-param each (D-11/D-12).
- `include/shards.h` + `src/shards.cpp` — SHARDS class + `--shards-exact` path at main.cpp:334; reused verbatim for D-03.
- `src/trace_gen.cpp::generate_zipf_trace(num_requests, num_objects, alpha, seed)` — produces exactly the 1M α=0.8 synthetic needed for D-15; seed=42 matches Phase 1 default.
- `scripts/plot_results.py` POLICY_COLORS/MARKERS dict — add 4 entries per D-08/D-11/D-12 Claude's-discretion styling.
- `Makefile` TEST_SRC/TEST_OBJDIR pattern (lines 79-96) — reused for `tests/test_doorkeeper.cpp` (D-12 test plan).
- `scripts/check_wtlfu_acceptance.py` — pattern for "3-condition acceptance gate emits exit code"; Phase 4 may want an analogous `scripts/check_dk_ablation.py` for DOOR-03 verification, but not required.

### Established Patterns
- **Ctor-param + factory-branch for policy variants** — Phase 2 widened `make_policy` signature and added one `wtinylfu` branch; Phase 4 reuses exactly this shape for `wtinylfu-dk`, `s3fifo-{5,10,20}`, `sieve-noprom`.
- **Default-preserves-back-compat in ctor parameters** — every new ctor param defaults to the Phase 1/2 hardcoded value; Phase 1 sweeps regenerate identically when re-run without new flags.
- **Per-workload result subdirs with flat file layout** — `results/{workload}/*.csv` + `figures/`; no nested subdirs (Phase 3 D-14).
- **Sweep CSVs share a schema across workloads** — `mrc.csv` header identical for Congress + Court; `ablation_*.csv` follows suit.
- **Tests are standalone assertion binaries with separate build/test/ dir** — Phase 2 D-04/D-07; Phase 4 extends Makefile's `test` target to build `test_doorkeeper` alongside `test_wtinylfu`.
- **Stats single-source invariant (L-12 from Phase 2)** — Doorkeeper is a sub-component of WTinyLFUCache; DK must NEVER call `record()` or touch `stats`. Grep gate: `grep -cE "record\(\s*(true|false)" include/wtinylfu.h` still equals 4.
- **CLI flag parsing pattern** — `main.cpp:92-125` for-loop with `std::strcmp` + `i+1<argc` guard; `--shards-rates` follows the `--cache-sizes` shape at line 100-102 (comma-split with `split(argv[++i], ',')`).

### Integration Points
- `include/cache.h` — S3FIFOCache ctor signature change (1 line) + SIEVECache ctor signature change (1 line) + 1-line hit-path guard in SIEVE access().
- `include/count_min_sketch.h` — 1 new member `std::function<void()> on_age_cb_`, 1 new method `set_on_age_cb(std::function<void()>)`, 1 callback fire at end of `halve_all_()`.
- `include/wtinylfu.h` — ctor gains bool param; ctor body conditionally instantiates `doorkeeper_` + registers CMS callback; access() gains pre-CMS-record filter branch.
- `src/main.cpp` — 4 new `make_policy` branches (D-08/D-11/D-12); 1 new CLI flag parse (D-18); policy-label map extensions; SHARDS rate grid now variable-sourced.
- `Makefile` — 4 new targets + 1 composition target (D-17); `test` target extended with `test_doorkeeper` dependency (D-12 tests).
- `scripts/plot_results.py` — 4 new `plot_<name>` functions registered in `main()` around line 307-314.
- No changes to `include/hash_util.h`, `include/trace_gen.h`, `include/shards.h`, `include/workload_stats.h`, `src/shards.cpp`, `src/trace_gen.cpp`, `src/workload_stats.cpp`, or `scripts/collect_*.py`.

</code_context>

<specifics>
## Specific Ideas

- **Baseline W-TinyLFU is invariant under Phase 4.** The entire Phase 2 admission logic, SLRU split, CMS update rule, and ctor default behavior is UNCHANGED. `--policies wtinylfu` in a post-Phase-4 sweep must produce identical numbers to the Phase 2 validation sweep. Grep gate: the existing 4-`record()` invariant in wtinylfu.h still holds after DK integration because the pre-CMS filter (D-05) sits BEFORE `cms_.record(key)` but AFTER the `access()` entry — no new `record()` calls are added.
- **Doorkeeper is a sibling variant, not a replacement.** The 6-policy validation grid from Phase 2/3 stays as 6 policies. The DOOR-03 ablation figure is a separate figure showing wtinylfu vs. wtinylfu-dk at identical cache sizes and alpha — NOT a replacement for the main MRC figure.
- **`s3fifo` remains the default display name** in the main MRC/alpha figures; `s3fifo-5` and `s3fifo-20` only appear in the ablation figure. Likewise `sieve` remains default; `sieve-noprom` only in the ablation figure. This keeps the Phase 6 writeup's main results tables from ballooning.
- **The 50K oracle slice is the FIRST 50K rows** of the 1M synthetic trace (deterministic, no resampling). The existing `--shards-exact` path loads and processes a trace in full — the plan should shim this via a `--limit N` flag or a one-off `head -50001` trace file; prefer the flag (one-liner in main.cpp).
- **`ablation_doorkeeper.csv` schema** follows `mrc.csv` plus a `variant` column: `cache_frac, cache_size_bytes, policy, variant, miss_ratio, byte_miss_ratio, accesses_per_sec` where `variant ∈ {baseline, +doorkeeper}`. Keeps Phase 5's `compare_workloads.py` join trivial.
- **PITFALLS M4 second bullet — "Also plot the SHARDS vs. exact MRC at the lowest rate where it's still accurate"** — is the `shards_mrc_overlay.pdf` in D-16. Figure shows: exact MRC (50K subslice) as solid line, SHARDS at {0.1%, 1%, 10%} as dashed lines overlaid on the same axes. The money shot of the SHARDS contribution (PITFALLS M4 literal).
- **Doorkeeper destructor / RAII** — DK owns its bit array `std::vector<uint64_t>`; standard destruction. No file handles, no allocations outside the vector. `reset()` calls `doorkeeper_.clear()` when DK is active.
- **Seed discipline for the 1M trace** — use `seed=42` matching Phase 1's default everywhere (generate_zipf_trace default). Any Phase 6 regeneration will produce identical bytes. The generate-script takes no `--seed` flag in its first cut; if needed, the default is overridable.
- **`accesses_per_sec` column** on ablation CSVs (Phase 1 REFACTOR-03) is populated but probably boring — all the policies run in the same throughput regime. Keeping the column for schema consistency; Phase 5 may use it for a Pareto plot.

</specifics>

<deferred>
## Deferred Ideas

- **Balanced-BST exact stack-distance oracle** (replacing O(n²)) — PITFALLS M4 alternative. Deferred because the 50K sub-slice (D-03) solves the oracle-regime question without the engineering cost of an O(n log n) rewrite. A v2 or a Phase 6 enrichment if a reviewer asks for >50K exact baseline.
- **Doorkeeper at admission-gate (vs. pre-CMS)** — the alternative rejected in D-05. If a reviewer challenges the paper-faithful choice, we have the comparison ready as a secondary ablation in v2.
- **W-TinyLFU hill-climbing adaptive window** — permanently omitted (Phase 2 L-8; STACK.md §Rejected). Do not revisit.
- **Full Cartesian ablation matrix** (all 4 axes × both workloads × all alphas) — overkill for scoped ablation figures. Each ablation stays 1-dimensional against its own parameter.
- **Multi-seed runs for ablation variance bands** — Phase 5 (ANAL-02). Phase 4 ablations are single-seed (seed=42) — the ablation signal should dwarf seed-to-seed noise at 1% cache and alpha ≥ 0.8; Phase 5 confirms.
- **Extending `--shards-rates` to accept rates beyond 10%** (e.g., 25%, 50%) — overkill; the 4 standard rates are what the paper and writeup need.
- **`scripts/check_phase4_acceptance.py`** analogous to `check_wtlfu_acceptance.py` — nice-to-have but not blocking. If the planner wants a Phase 4 acceptance gate, it can be a 1-plan follow-on; otherwise the convergence CSV + ablation figure speak for themselves.
- **Caffeine Doorkeeper cross-validation** (running DK variant against a Caffeine-published workload) — V2-01 in REQUIREMENTS.md, already deferred at milestone planning.
- **Committing `traces/shards_large.csv`** — rejected in D-15. Revisit only if demo laptop regen exceeds 30s (PITFALLS C7 threshold).
- **Doorkeeper false-positive-rate tuning study** — overkill; STACK.md's 4-bits-per-element default is fine for the ablation figure.

</deferred>

<scope_creep_log>
## Scope Creep Redirected

None during this discussion — the user engaged with the 4 gray areas as bounded implementation choices and did not propose new capabilities. All 4 axes stay within ROADMAP Phase 4 success criteria.

</scope_creep_log>

---

*Phase: 04-shards-large-scale-validation-ablations*
*Context gathered: 2026-04-20*
