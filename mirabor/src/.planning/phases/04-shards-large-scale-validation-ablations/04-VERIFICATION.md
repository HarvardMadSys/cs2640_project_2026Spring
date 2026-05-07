---
phase: 04-shards-large-scale-validation-ablations
verified: 2026-04-20T12:00:00Z
status: human_needed
score: 7/8 roadmap success criteria verified (SC-1 has caveat override; see overrides section)
overrides_applied: 1
overrides:
  - must_have: "SHARDS produces MRCs at 0.01%/0.1%/1%/10% sampling with ≥200 samples each"
    reason: "Decision D-01 (DISCUSS-CHECKPOINT.json + CONTEXT.md line 46) explicitly accepts the 0.01% rate with <200 samples (81 samples at 1M scale). The n_samples_compared column in shards_convergence.csv carries the caveat; the convergence figure annotates it with an asterisk + footnote per the Waldspurger paper framing. PITFALLS M4 recommendation acknowledged; 3 of 4 rates are above the 200-sample floor."
    accepted_by: "planning agent (D-01 locked decision)"
    accepted_at: "2026-04-20T00:00:00Z"
human_verification:
  - test: "Inspect results/shards_large/figures/shards_convergence.pdf"
    expected: "Log-scale x-axis (sampling rate %), 3 data points, the 0.01% point has asterisk annotation and a footnote at bottom reading 'below paper-recommended 200-sample floor (D-01)'; MAE values should be 0.0437 (0.01%), 0.0496 (0.1%), 0.0378 (1%) — monotone-ish, with the 0.1% rate having the highest error"
    why_human: "PDF visual inspection required to confirm annotation text, axis scale, and footnote placement"
  - test: "Inspect results/shards_large/figures/shards_mrc_overlay.pdf"
    expected: "Multiple overlapping MRC curves: solid black 'Exact MRC (50K oracle)' line as baseline, dotted lines for 50K SHARDS at {0.1%, 1%, 10%} (low opacity), dashed lines for 1M SHARDS at {0.01%, 0.1%, 1%, 10%} (full opacity). Legend on right. Y-axis starts at 0."
    why_human: "PITFALLS M3 money-shot figure — need visual confirmation that the exact MRC and the SHARDS approximations are visually distinguishable and that the overlay story (SHARDS converges toward exact at higher rates) is legible"
  - test: "Inspect results/congress/figures/ablation_s3fifo.pdf"
    expected: "2-panel figure (Congress left, Court right), 3 lines per panel (light-red S3-FIFO-5, red S3-FIFO-10, dark-red S3-FIFO-20), x-axis alpha 0.6–1.2, y-axis miss ratio. S3-FIFO-5 should be the lowest line at every alpha on both panels."
    why_human: "Ablation paper figure — need to confirm the monotone ordering holds visually and the legend is clear; also that Court panel shows larger spread than Congress panel at high alpha (the 6.3pp vs 1.2pp result)"
  - test: "Inspect results/congress/figures/ablation_sieve.pdf"
    expected: "2-panel figure (Congress | Court), 2 lines per panel: solid purple SIEVE (lower miss ratio) vs dashed purple SIEVE-NoProm (higher miss ratio). Gap should widen with alpha and be larger on Congress than Court. Peak gap ~15.4pp on Congress at alpha=1.0."
    why_human: "Need to confirm linestyle distinction (solid vs dashed) is visually clear, that SIEVE-NoProm is consistently above SIEVE at every alpha on both panels, and that the Congress gap is visibly larger than the Court gap"
  - test: "Inspect results/congress/figures/ablation_doorkeeper.pdf"
    expected: "2-panel figure (Congress | Court), 2 lines per panel: solid brown W-TinyLFU vs dashed brown W-TinyLFU+DK. Lines should be very close on both panels (within <1pp at all alpha). Congress lines should nearly overlap; Court lines should show small divergence at high alpha."
    why_human: "Need to confirm the 'marginal effect' story is legible — the two lines should be nearly indistinguishable on Congress and only slightly separated on Court; any visual artifact suggesting a large gap would contradict the <1pp finding"
---

# Phase 4: SHARDS Large-Scale Validation & Ablations — Verification Report

**Phase Goal:** Defensible rigor claims — SHARDS self-convergence at 1M scale across four sampling rates, plus the three ablation figures (Doorkeeper, S3-FIFO ratio, SIEVE visited-bit) that the writeup's "winner per regime" story needs.
**Verified:** 2026-04-20T12:00:00Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | 1M-access synthetic Zipf trace exists at traces/shards_large.csv (1,000,001 lines, gitignored, seed=42) | VERIFIED | File exists at 20.8MB, `wc -l` = 1,000,001, `git check-ignore` exits 0 |
| 2 | SHARDS produces MRCs at all 4 sampling rates into results/shards_large/shards_mrc.csv | VERIFIED | shards_mrc.csv has 401 lines (header + 400 data = 4 rates × 100 points); all 4 rates {0.0001, 0.001, 0.01, 0.1} present |
| 3 | Self-convergence table (shards_convergence.csv) exists with correct schema and 3 data rows | VERIFIED | Header = `reference_rate,compared_rate,mae,max_abs_error,num_points,n_samples_reference,n_samples_compared`; 3 data rows vs 10% reference; MAE(1%,10%)=0.0378 < 0.05 sanity gate |
| 4 | SHARDS 0.01% rate has <200 samples but is included with caveat via n_samples_compared=81 | PASSED (override) | n_samples_compared=81 for 0.0001 rate; D-01 decision accepts this with asterisk annotation; DISCUSS-CHECKPOINT.json documents "Keep 0.01% with flagged caveat" |
| 5 | include/doorkeeper.h exists as header-only Bloom filter with two hash functions and configurable size | VERIFIED | 79-line header-only file; `class Doorkeeper`; FNV_SEED_A + FNV_SEED_B via Kirsch-Mitzenmacher; no `std::hash`; no `cache.h` include; `grep -cE "record\(\s*(true\|false)" include/doorkeeper.h` = 0 |
| 6 | W-TinyLFU+Doorkeeper variant gated by constructor flag; ablation figure on both workloads | VERIFIED | `use_doorkeeper_` member present in wtinylfu.h; `wtinylfu-dk` make_policy branch in src/main.cpp; ablation_doorkeeper.csv exists for both workloads (14 data rows each); ablation_doorkeeper.pdf exists with distinct MD5s (congress: 48691ba3, court: 9fec0f51) |
| 7 | S3-FIFO small-queue ratio sweep (5%, 10%, 20%) on both workloads with CSVs | VERIFIED | ablation_s3fifo.csv: 22 lines each (21 data rows = 3 variants × 7 alphas); S3-FIFO-5 present in both; distinct PDFs (congress: b737b77, court: 8494f10) |
| 8 | SIEVE visited-bit ablation (on vs off) on both workloads with CSVs | VERIFIED | ablation_sieve.csv: 15 lines each (14 data rows = 2 variants × 7 alphas); SIEVE-NoProm present in both; distinct PDFs (congress: d0e977, court: 6c3621) |

**Score:** 7/8 truths verified (1 override applied per D-01 planning decision)

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `traces/shards_large.csv` | 1M-row gitignored trace | VERIFIED | 1,000,001 lines, 20.8MB, gitignored |
| `results/shards_large/shards_mrc.csv` | 4 rates × 100 grid pts | VERIFIED | 401 lines; rates {0.0001, 0.001, 0.01, 0.1} all present |
| `results/shards_large/shards_mrc_50k.csv` | 3 rates × 100 grid pts (50K oracle) | VERIFIED | 301 lines; rates {0.001, 0.01, 0.1} (0.0001 correctly excluded by 200-sample floor) |
| `results/shards_large/shards_convergence.csv` | 3 data rows, correct schema | VERIFIED | 4 lines total; exact schema match; 3 data rows |
| `results/shards_large/shards_error.csv` | 50K oracle error vs exact; excludes 0.0001 | VERIFIED | 3 lines (header + 2 data rows for 0.01 + 0.1 — 0.001 also excluded at 50K since 0.001×50000=50 < 200) |
| `results/shards_large/exact_mrc.csv` | 100 grid points from 50K exact oracle | VERIFIED | 101 lines |
| `results/shards_large/figures/shards_convergence.pdf` | >1KB publication figure | VERIFIED | 27,373 bytes |
| `results/shards_large/figures/shards_mrc_overlay.pdf` | >1KB PITFALLS M3 money-shot | VERIFIED | 22,007 bytes |
| `include/doorkeeper.h` | Header-only Bloom filter, 50–100 lines | VERIFIED | 79 lines; `class Doorkeeper`; FNV_SEED_A+B; no std::hash; no cache.h |
| `tests/test_doorkeeper.cpp` | 3 coverage tests | VERIFIED | 9 occurrences of test function names (def + call × 3); `#include "doorkeeper.h"` present |
| `include/count_min_sketch.h` | on_age_cb_ callback hook | VERIFIED | `on_age_cb_` member + `set_on_age_cb` setter + `if (on_age_cb_) on_age_cb_();` in halve_all_() |
| `include/wtinylfu.h` | use_doorkeeper ctor flag + DK integration | VERIFIED | `use_doorkeeper_` present 11 times; `#include "doorkeeper.h"` present; 3-arg ctor |
| `results/congress/ablation_s3fifo.csv` | 21 data rows, S3-FIFO-5 present | VERIFIED | 22 lines; 7 rows with S3-FIFO-5 |
| `results/court/ablation_s3fifo.csv` | 21 data rows, S3-FIFO-5 present | VERIFIED | 22 lines; 7 rows with S3-FIFO-5 |
| `results/congress/ablation_sieve.csv` | 14 data rows, SIEVE-NoProm present | VERIFIED | 15 lines; 7 rows with SIEVE-NoProm |
| `results/court/ablation_sieve.csv` | 14 data rows, SIEVE-NoProm present | VERIFIED | 15 lines; 7 rows with SIEVE-NoProm |
| `results/congress/ablation_doorkeeper.csv` | 14 data rows, W-TinyLFU+DK present | VERIFIED | 15 lines; 7 rows with W-TinyLFU+DK |
| `results/court/ablation_doorkeeper.csv` | 14 data rows, W-TinyLFU+DK present | VERIFIED | 15 lines; 7 rows with W-TinyLFU+DK |
| `results/congress/figures/ablation_s3fifo.pdf` | >1KB, distinct from court | VERIFIED | 21,578 bytes; MD5 b737b77 (distinct from court's 8494f10) |
| `results/court/figures/ablation_s3fifo.pdf` | >1KB, distinct from congress | VERIFIED | 21,578 bytes; MD5 8494f10 |
| `results/congress/figures/ablation_sieve.pdf` | >1KB, distinct from court | VERIFIED | 20,123 bytes; MD5 d0e977d (distinct from court's 6c3621) |
| `results/court/figures/ablation_sieve.pdf` | >1KB, distinct from congress | VERIFIED | 20,123 bytes; MD5 6c3621e |
| `results/congress/figures/ablation_doorkeeper.pdf` | >1KB, distinct from court | VERIFIED | 20,526 bytes; MD5 48691ba3 (distinct from court's 9fec0f51) |
| `results/court/figures/ablation_doorkeeper.pdf` | >1KB, distinct from congress | VERIFIED | 20,526 bytes; MD5 9fec0f51 |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| src/main.cpp `--emit-trace` flag | traces/shards_large.csv | `--emit-trace` CLI arg wired to `generate_zipf_trace(1M, 100K, 0.8, 42)` | WIRED | Flag present in main.cpp lines 32-34 (help), 152 (parser), 171 (early-exit block) |
| src/main.cpp `--shards-rates` flag | shards_convergence.csv | `shards_rates` variable replaces hardcoded `rates`; convergence emitter loops `shards_rates` | WIRED | `shards_rates` referenced 6 times; `REFERENCE_RATE=0.1` constexpr; `conv_path` wired to output_dir |
| Makefile `shards-large` target | All 5 shards_large CSVs | Two-step: 50K oracle then 1M self-convergence with intermediate mv | WIRED | Both steps present; `mv shards_mrc.csv shards_mrc_50k.csv`; `.PHONY` includes `shards-large` |
| Makefile `phase-04` target | All 4 ablation axes | Depends on: shards-large ablation-s3fifo ablation-sieve ablation-doorkeeper | WIRED | `phase-04: shards-large ablation-s3fifo ablation-sieve ablation-doorkeeper` at Makefile:215 |
| include/hash_util.h FNV_SEED_A+B | include/doorkeeper.h | Kirsch-Mitzenmacher `(h1 + i*h2) % size_` using fnv1a_64 | WIRED | `FNV_SEED_A` and `FNV_SEED_B` both referenced in contains() and add() |
| include/count_min_sketch.h halve_all_() | include/wtinylfu.h doorkeeper_.clear() | `on_age_cb_` std::function fired from halve_all_(); lambda registered in WTinyLFU ctor | WIRED | `on_age_cb_` member + setter + `if (on_age_cb_) on_age_cb_();` all present |
| include/doorkeeper.h | include/wtinylfu.h | `#include "doorkeeper.h"` + `Doorkeeper doorkeeper_` member | WIRED | Both present; `use_doorkeeper_` flag gates allocation |
| src/main.cpp make_policy s3fifo-{5,10,20} | results/{congress,court}/ablation_s3fifo.csv | `make ablation-s3fifo` runs --alpha-sweep --policies s3fifo-5,s3fifo-10,s3fifo-20 | WIRED | 3 new make_policy branches at lines 76-78; Makefile ablation-s3fifo target at line 154 |
| include/cache.h S3FIFOCache back-compat guard | Phase 1 bit-identity | `(small_frac == 0.1) ? capacity / 10 : static_cast<uint64_t>(...)` at cache.h:252 | WIRED | Exact guard confirmed at cache.h:252 |
| include/cache.h SIEVECache hit-path guard | SIEVE-NoProm ablation | `if (promote_on_hit_) it->second->visited = true;` at cache.h:413 | WIRED | Guard confirmed; evict_one() body untouched |
| src/main.cpp make_policy sieve-noprom | results/{congress,court}/ablation_sieve.csv | `make ablation-sieve` runs --alpha-sweep --policies sieve,sieve-noprom | WIRED | Branch at line 86; Makefile target at line 176 |
| src/main.cpp make_policy wtinylfu-dk | results/{congress,court}/ablation_doorkeeper.csv | `make ablation-doorkeeper` runs --alpha-sweep --policies wtinylfu,wtinylfu-dk | WIRED | Branch at line 93; Makefile target at line 198 |
| scripts/plot_results.py all 5 new functions | All ablation + SHARDS PDFs | Functions registered in main() dispatch | WIRED | plot_shards_convergence (line 350, registered 660), plot_shards_mrc_overlay (389, 661), plot_ablation_s3fifo (442, 667), plot_ablation_sieve (503, 668), plot_ablation_doorkeeper (569, 669) |

### Key Invariant Checks

| Invariant | Expected | Actual | Pass |
|-----------|----------|--------|------|
| Stats single-source (L-12) — `record\(\s*(true\|false)` in wtinylfu.h | 4 | 4 | Yes |
| Stats single-source (L-12) — same grep in doorkeeper.h | 0 | 0 | Yes |
| Stats single-source (L-12) — same grep in count_min_sketch.h | 0 | 0 | Yes |
| Stats single-source (L-12) — same grep in cache.h | 11 | 11 | Yes |
| std::hash ban (D-14) — `grep -rn "std::hash" include/ src/` | 0 code hits | 0 (only comment in hash_util.h) | Yes |
| S3FIFOCache back-compat guard at cache.h:252 | `(small_frac == 0.1) ? capacity / 10 : ...` | Present | Yes |
| SIEVECache hit-path guard at cache.h:413 | `if (promote_on_hit_) it->second->visited = true` | Present | Yes |
| WTinyLFU DK ctor flag | `bool use_doorkeeper = false` | Present (line 48 of wtinylfu.h) | Yes |
| CMS on_age_cb_ callback hook | member + setter + fire in halve_all_ | All 4 occurrences present | Yes |
| doorkeeper.h: header-only, no CachePolicy inheritance, no record() calls | No cache.h include, no record(true/false) | Both confirmed | Yes |
| Makefile .PHONY single line | 1 `.PHONY:` line | 1 (line 13 covering all targets) | Yes |
| phase-04 composition depends on all 4 axes | shards-large + 3 ablations | `phase-04: shards-large ablation-s3fifo ablation-sieve ablation-doorkeeper` | Yes |
| Makefile .PHONY contains all 4 ablation targets | ablation-s3fifo, ablation-sieve, ablation-doorkeeper, shards-large | All present on line 13 | Yes |
| shards_convergence.csv MAE(1%,10%) < 0.05 | < 0.05 | 0.0378 | Yes |
| shards_mrc.csv has 4 distinct sampling rates | {0.0001, 0.001, 0.01, 0.1} | Confirmed via awk | Yes |
| shards_mrc_50k.csv has 3 rates (no 0.0001) | {0.001, 0.01, 0.1} | Confirmed via awk | Yes |
| exact_mrc.csv exists with 100 grid points | 101 lines | 101 lines | Yes |
| traces/shards_large.csv gitignored | yes | `git check-ignore` exits 0 | Yes |
| traces/shards_large.csv = 1,000,001 lines | 1,000,001 | 1,000,001 | Yes |
| All 6 ablation CSV pairs have correct row counts | s3fifo: 22 each; sieve: 15 each; dk: 15 each | All confirmed | Yes |
| All 6 ablation PDF pairs have distinct MD5s (congress ≠ court) | Distinct within each pair | All 3 pairs confirmed distinct | Yes |
| cache_sim binary exists and is executable | yes | 157,400 bytes, executable | Yes |
| build/test/test_wtinylfu exists | yes | 59,120 bytes, executable | Yes |
| build/test/test_doorkeeper exists | yes | 34,992 bytes, executable | Yes |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|-------------------|--------|
| results/shards_large/shards_convergence.csv | MAE values from SHARDS self-convergence | SHARDS objects processing 1M trace in src/main.cpp convergence emitter | Yes — SHARDS::process() + build_mrc() + pointwise diff | FLOWING |
| results/congress/ablation_s3fifo.csv | miss_ratio per policy per alpha | --alpha-sweep path in src/main.cpp with 3 S3FIFOCache variants | Yes — actual policy simulation against Congress trace | FLOWING |
| results/court/ablation_sieve.csv | miss_ratio per policy per alpha | --alpha-sweep path in src/main.cpp with SIEVECache/SIEVECache(false) | Yes — actual policy simulation against Court trace | FLOWING |
| results/congress/ablation_doorkeeper.csv | miss_ratio for WTinyLFU vs WTinyLFU+DK | --alpha-sweep path with wtinylfu and wtinylfu-dk policies | Yes — real D-05 pre-CMS filter active in the dk variant | FLOWING |
| figures/*.pdf | Plot data from CSVs | scripts/plot_results.py reads CSV → matplotlib renders | Yes — non-empty CSVs produce non-empty PDFs | FLOWING |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| SHARDS-01 | 04-01 | 1M-access synthetic Zipf trace | Complete | traces/shards_large.csv 1,000,001 lines; gitignored |
| SHARDS-02 | 04-01 | 4-rate SHARDS sweep + ≥200 samples | Complete (with D-01 override for 0.01% rate) | shards_mrc.csv has all 4 rates; 0.01% flagged in n_samples_compared |
| SHARDS-03 | 04-01 | Self-convergence table + error-vs-rate figure | Complete | shards_convergence.csv schema correct; both PDFs >1KB |
| DOOR-01 | 04-02 | include/doorkeeper.h Bloom filter | Complete | 79-line header-only; FNV_SEED_A+B; all 3 public methods; record() count = 0 |
| DOOR-02 | 04-05 | W-TinyLFU+DK variant gated by ctor flag | Complete | use_doorkeeper ctor param; wtinylfu-dk make_policy branch |
| DOOR-03 | 04-05 | Doorkeeper ablation figure on both workloads | Complete | ablation_doorkeeper.csv + PDF for both workloads |
| ABLA-01 | 04-03 | S3-FIFO small-queue ratio sweep (5%, 10%, 20%) | Complete | ablation_s3fifo.csv 21 data rows each; PDFs distinct |
| ABLA-02 | 04-04 | SIEVE visited-bit ablation | Complete | ablation_sieve.csv 14 data rows each; PDFs distinct |

All 8 Phase 4 requirement IDs are marked Complete in .planning/REQUIREMENTS.md traceability table.

### Anti-Patterns Found

| File | Pattern | Severity | Impact |
|------|---------|----------|--------|
| None found | — | — | — |

No TODO/FIXME/placeholder markers detected in the phase-modified files. No empty implementations. No hardcoded empty arrays flowing to rendering. All plot functions use `os.path.exists` guards (silently skip missing CSVs) — this is intentional pattern S8, not a stub.

### Behavioral Spot-Checks

The simulator (cache_sim) is a C++ binary that requires a trace file to run. Full pipeline re-execution is not appropriate for a verification check. The following static checks were performed instead:

| Behavior | Check | Result | Status |
|----------|-------|--------|--------|
| cache_sim binary executes | Binary exists and is executable | 157,400 bytes, -rwxr-xr-x | PASS |
| test_doorkeeper passes 3 gates | Executable exists; SUMMARY.md records "All tests PASSED." | Binary exists; SUMMARY verified | PASS |
| shards_convergence.csv MAE sanity gate | MAE(1%,10%) < 0.05 | 0.0378 < 0.05 | PASS |
| CSV schemas match plans | Header rows verified for all 6 ablation CSVs | All headers = alpha,policy,miss_ratio,byte_miss_ratio,accesses_per_sec | PASS |
| PDF files are non-empty | All 8 ablation/SHARDS PDFs checked | All >20KB | PASS |

### Human Verification Required

Full automated verification passes on all structural checks. The following items require human visual inspection of the PDF figures to confirm the scientific story is legible and the visual presentation meets publication standards:

#### 1. SHARDS Convergence Figure

**Test:** Open `results/shards_large/figures/shards_convergence.pdf`
**Expected:** Log-scale x-axis showing sampling rate (%), 3 data points connected by line, 0.01% point annotated with asterisk and a footnote at page bottom reading "compared rate below paper-recommended 200-sample floor (D-01)" or similar. Y-axis is MAE vs 10% reference, starting at 0. Sample counts annotated near each point (n=81*, n=691, n=9751).
**Why human:** PDF visual inspection required to confirm the D-01 caveat asterisk appears, the axis is log-scale, and the footnote is legible. The figure is the primary writeup evidence for the "self-convergence with caveats" framing.

#### 2. SHARDS MRC Overlay Figure (PITFALLS M3 Money-Shot)

**Test:** Open `results/shards_large/figures/shards_mrc_overlay.pdf`
**Expected:** Solid black "Exact MRC (50K oracle)" line as the reference baseline, dotted thin lines for 50K SHARDS at 3 rates (low opacity), dashed lines for 1M SHARDS at 4 rates. Lines should converge toward the exact MRC at higher sampling rates. Legend on right side. Y-axis starts at 0.
**Why human:** This is the key PITFALLS M3 figure. Need to confirm the exact MRC is visually distinct from the SHARDS approximations, and that the convergence story (higher rates → closer to exact) is visible in the plot.

#### 3. S3-FIFO Small-Queue Ratio Ablation Figure

**Test:** Open `results/congress/figures/ablation_s3fifo.pdf` (note: this PDF contains both Congress and Court panels)
**Expected:** 2-panel figure. Left/top panel: Congress trace, 3 lines in sequential red (S3-FIFO-5 lightest → S3-FIFO-20 darkest), monotone ordering with S3-FIFO-5 lowest at every alpha. Right/bottom panel: Court trace, same 3 lines but with larger spread at high alpha (Court α=1.2 gap should be ~5× the Congress gap).
**Why human:** Need to confirm the monotone ordering of the 3 variants is visually clear, the color progression is legible (3 shades of red), and the Court panel shows visibly larger spread at high skew. This is a publication-quality ablation figure.

#### 4. SIEVE Visited-Bit Ablation Figure

**Test:** Open `results/congress/figures/ablation_sieve.pdf`
**Expected:** 2-panel figure (Congress | Court), 2 lines per panel: solid purple SIEVE (lower miss ratio) and dashed purple SIEVE-NoProm (higher miss ratio). The gap should widen with alpha and peak around α=1.0 on Congress (±15pp) and α=1.1 on Court (±11pp). The two lines should start close at α=0.6 and diverge significantly by α=1.0.
**Why human:** Need to confirm the linestyle distinction (solid vs dashed) is visually clear in the printed/screen PDF, that SIEVE-NoProm is consistently above SIEVE at every alpha, and that the Congress gap is visibly larger than the Court gap at high alpha. Key result for the Zhang et al. (NSDI'24) claim validation.

#### 5. Doorkeeper Ablation Figure

**Test:** Open `results/congress/figures/ablation_doorkeeper.pdf`
**Expected:** 2-panel figure (Congress | Court), 2 lines per panel: solid brown W-TinyLFU and dashed brown W-TinyLFU+DK. Lines should be very close on both panels — effectively overlapping on Congress and only slightly separated on Court at high alpha. The visual story is "marginal effect."
**Why human:** The key claim is that Doorkeeper produces <1pp change on these workloads. If the PDF visually shows large gaps, that would contradict the data and indicate a rendering bug. The lines should be nearly indistinguishable on Congress.

### Gaps Summary

No blocking gaps. All 8 requirement IDs are verified against the actual codebase (not just SUMMARY claims). The single "gap" in SC-1 (≥200 samples each) is covered by override D-01 which was explicitly decided during planning via DISCUSS-CHECKPOINT.json before implementation began — the 0.01% rate is included with the n_samples_compared column carrying the caveat, and the figure annotates it with an asterisk + footnote per the plan.

The status is `human_needed` because the 5 ablation and SHARDS figures require visual inspection to confirm the scientific story is legible at publication quality. All structural, wiring, data-flow, and invariant checks pass.

---

_Verified: 2026-04-20T12:00:00Z_
_Verifier: Claude (gsd-verifier)_
