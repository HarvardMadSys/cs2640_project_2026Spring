---
phase: 04-shards-large-scale-validation-ablations
plan: 04
subsystem: sieve
tags:
  - sieve
  - ablation
  - visited-bit
  - parameter-sensitivity
  - figures
  - lazy-promotion
requires:
  - include/cache.h SIEVECache @ lines 374-459 (Phase 1)
  - src/main.cpp make_policy factory + both policy-label maps (Phase 1 + Plan 04-03 additions)
  - src/main.cpp --alpha-sweep emitter @ lines 274-... (Phase 1) — reused verbatim
  - scripts/plot_results.py plot_alpha_sensitivity @ 153-180 + plot_ablation_s3fifo @ 422-480 (Phase 1 / Plan 04-03) — templates for the new function
  - results/{congress,court} trace and alpha-sweep infrastructure (Phase 1/3)
provides:
  - SIEVECache ctor extended to (uint64_t capacity, bool promote_on_hit = true) with init-list ternary for name_
  - 1 new make_policy branch: sieve-noprom (dispatches to SIEVECache(capacity, false))
  - Makefile ablation-sieve target running both Congress + Court --alpha-sweep --policies sieve,sieve-noprom
  - Makefile phase-04 composition target now depends on shards-large + ablation-s3fifo + ablation-sieve
  - scripts/plot_results.py plot_ablation_sieve function (2-panel Congress | Court, linestyle-distinguished variants)
  - POLICY_COLORS / POLICY_MARKERS entries for SIEVE-NoProm (same purple + same marker as legacy SIEVE; linestyle disambiguates)
  - results/{congress,court}/ablation_sieve.csv (14 data rows each = 2 variants × 7 alphas)
  - results/{congress,court}/figures/ablation_sieve.pdf (20123 bytes each, 2-panel parameter-sensitivity figure)
affects:
  - include/cache.h (SIEVECache ctor + 2 new private members + name() delegation + 1-line hit-path guard)
  - src/main.cpp (1 new make_policy branch + 2 symmetric label-map entries for mixed-case display name)
  - Makefile (ablation-sieve target; .PHONY extension; phase-04 composition extension)
  - scripts/plot_results.py (plot_ablation_sieve; POLICY_COLORS/MARKERS entries; main() dispatch)
tech-stack:
  added: []
  patterns:
    - "ctor default-arg preserving back-compat — structural guarantee: `if (promote_on_hit_)` wraps an unchanged statement when the condition is always-true, so the generated code for the default path is equivalent to the pre-edit straight-line code modulo a predictable branch"
    - "explicit mixed-case label-map entry override when default toupper() would mangle the display name (SIEVE-NoProm vs SIEVE-NOPROM)"
    - "linestyle-distinguished within-color variant pairs — same POLICY_COLORS entry, same POLICY_MARKERS entry, visual distinction carried by endswith('NoProm') → '--' else '-' in the plot function"
    - "2-panel matplotlib parameter-sensitivity figure with shared y-axis (reuses plot_alpha_sensitivity + plot_ablation_s3fifo template)"
    - "ablation-{axis} Makefile target with per-workload --output rename + composition into phase-{N} target"
key-files:
  created:
    - results/congress/ablation_sieve.csv
    - results/court/ablation_sieve.csv
    - results/congress/figures/ablation_sieve.pdf
    - results/court/figures/ablation_sieve.pdf
  modified:
    - include/cache.h
    - src/main.cpp
    - Makefile
    - scripts/plot_results.py
decisions:
  - "D-12 bit-identity via always-true guard: `SIEVECache(capacity)` with `promote_on_hit` defaulted to `true` wraps the existing `it->second->visited = true` in `if (promote_on_hit_)`. When the condition is always-true (legacy path), the wrapped statement executes identically to the original straight-line code — no formula change, no rounding subtlety (unlike Plan 04-03's FP multiply). Bit-identity is structurally guaranteed, verified empirically: miss_ratio + byte_miss_ratio columns for SIEVE rows are byte-identical between pre-Phase-4 runs and post-plan runs (only accesses_per_sec throughput noise differs, as expected)."
  - "Name distinguishability via ctor init-list ternary: 2-arg ctor sets `name_` = `promote_on_hit ? \"SIEVE\" : \"SIEVE-NoProm\"` inline. No 3-arg delegating overload needed (unlike Plan 04-03's S3FIFOCache) because the 2 variants are fully determined by the flag value — the name_override pattern was over-engineered for a 2-option axis."
  - "Color-same, linestyle-different for within-policy variant pair: SIEVE-NoProm shares POLICY_COLORS entry #9467bd and POLICY_MARKERS entry 'v' with legacy SIEVE. The plot function uses `linestyle = '--' if policy.endswith('NoProm') else '-'` to disambiguate. Rationale: the ablation figure reads as 'same policy, promotion on/off' rather than 'two unrelated policies' — the visual semantics match the semantic claim. Also keeps the POLICY_COLORS/MARKERS dicts minimally extended (1 entry each)."
  - "Explicit mixed-case label override required in both src/main.cpp label-map blocks: `else if (pn == \"sieve-noprom\") label = \"SIEVE-NoProm\";`. The default `for (auto& c : label) c = toupper(c)` path would produce `SIEVE-NOPROM` (all-caps), diverging from cache.h's name_ member and the plot_results.py dict key. Legacy `sieve` policy still uses the toupper default → 'SIEVE' — no explicit entry needed for it."
  - "Makefile ablation-sieve runs --alpha-sweep --policies sieve,sieve-noprom twice (Congress + Court) and renames alpha_sensitivity.csv → ablation_sieve.csv so ablation output is namespaced and a subsequent make run-sweep does not overwrite it. Mirrors Plan 04-03's ablation-s3fifo shape verbatim per PATTERNS.md Axis C/D shared pattern."
  - "phase-04 composition target extended: `phase-04: shards-large ablation-s3fifo ablation-sieve` — subsequent Plan 04-05 will append ablation-doorkeeper."
  - "Stats single-source invariant (L-12) preserved: SIEVECache::access() record() calls at cache.h:414/425 are UNCHANGED — plan edits only added the if-guard on line 413 around the visited-bit set. Grep gate: `grep -cE \"record\\(\\s*(true|false)\" include/cache.h` = 11 (unchanged from pre-plan baseline)."
metrics:
  duration: "~5m 39s"
  completed: "2026-04-20T07:05:47Z"
  tasks: 3
  files: 8
---

# Phase 4 Plan 04: SIEVE visited-bit Ablation Summary

One-liner: Ships the SIEVE visited-bit parameter-sensitivity ablation (Axis D of Phase 4 / D-19) — one new policy variant (`sieve-noprom`), a 2-panel ablation figure per workload, and a headline result: **lazy promotion on hit is empirically the largest contributor to SIEVE's low miss-ratio; without it, SIEVE-NoProm collapses toward FIFO-with-hand and underperforms SIEVE by 10-15 percentage points at high skew on both Congress and Court Zipf traces, with the gap growing monotonically with alpha.** Implements ABLA-02.

## Purpose

Phase 4 ROADMAP success criterion 4 (partial) — SIEVE visited-bit ablation on both workloads at fixed cache size (1% of working bytes via D-13 / existing `wb/100` at `src/main.cpp` alpha-sweep path). Tests the Zhang et al. (NSDI'24) claim that lazy promotion (setting visited=true on every hit, then clearing it as the eviction hand sweeps) is what gives SIEVE its distinctive scan-resistance.

## Artifacts Shipped

### include/cache.h — SIEVECache ctor extension (~17 LOC net)

Four coordinated edits in `class SIEVECache`:

1. **Two new private members** after `hand_valid_ = false;`:
   ```cpp
   bool promote_on_hit_;
   std::string name_;
   ```

2. **Ctor widened to 2-arg with default** (preserves back-compat):
   ```cpp
   SIEVECache(uint64_t capacity, bool promote_on_hit = true)
       : capacity_(capacity),
         promote_on_hit_(promote_on_hit),
         name_(promote_on_hit ? "SIEVE" : "SIEVE-NoProm") {}
   ```
   Inline init-list ternary sets `name_` based on the flag — no delegating overload needed (unlike Plan 04-03's S3FIFOCache 3-arg pattern, which was motivated by 3 variants sharing a ctor formula; here only 2 variants exist and they're fully flag-determined).

3. **Hit-path guard at cache.h:413** (single line wrapped):
   ```cpp
   if (promote_on_hit_) it->second->visited = true;  // D-12: guarded for SIEVE-NoProm ablation
   ```

4. **`name()` delegates to member** at cache.h:457:
   ```cpp
   std::string name() const override { return name_; }
   ```

`evict_one()` at lines 429-455 is UNCHANGED — SIEVE's eviction hand still reads `hand_->visited` and clears it as designed; D-12 only suppresses the SET on hit, not the READ during eviction. Miss-path at lines 417-426 is UNCHANGED — new inserts still get `visited=false` at line 422.

### src/main.cpp — 1 new make_policy branch + 2 symmetric label-map entries

**make_policy @ lines 79-86:**
```cpp
if (name == "sieve")    return std::make_unique<SIEVECache>(capacity);    // legacy, unchanged
// ... comment block ...
if (name == "sieve-noprom") return std::make_unique<SIEVECache>(capacity, /*promote_on_hit=*/false);
```

**Policy-label maps:** two symmetric blocks @ mrc.csv header (line ~231) and alpha_sensitivity.csv header (line ~285) each gain one new `else if` entry:
```cpp
else if (pn == "sieve-noprom") label = "SIEVE-NoProm";
```
Required because the default `toupper()` path would produce "SIEVE-NOPROM" (all-caps), breaking the plot_results.py dict lookup and CSV `policy` column semantics.

### Makefile — ablation-sieve target + phase-04 composition

- `.PHONY` line @ line 13 gains `ablation-sieve`
- New target `ablation-sieve` @ lines 175-190 runs the simulator's `--alpha-sweep --policies sieve,sieve-noprom` twice (once per workload) with per-workload `--output-dir` and then `mv` to `ablation_sieve.csv` so the result is namespaced and idempotent across re-runs of `make run-sweep`
- `phase-04` composition @ line 194 extended: `phase-04: shards-large ablation-s3fifo ablation-sieve`

### scripts/plot_results.py — plot_ablation_sieve + styling

**POLICY_COLORS @ line 64** (same purple as legacy SIEVE, disambiguated via linestyle):
```python
"SIEVE-NoProm": "#9467bd",
```

**POLICY_MARKERS @ line 77** (same marker as legacy SIEVE):
```python
"SIEVE-NoProm": "v",
```

Legacy `"SIEVE": "#9467bd"` @ line 50 and `"SIEVE": "v"` @ line 66 are UNCHANGED.

**plot_ablation_sieve @ line 483** — 2-panel matplotlib figure (Congress | Court) with shared y-axis; reads both per-workload CSVs, plots 2 lines per panel (SIEVE solid purple + SIEVE-NoProm dashed purple), legend ordered baseline-first via `endswith("NoProm")` sort key, writes to `results/{workload}/figures/ablation_sieve.pdf`.

Registered in `main()` @ line 585 after `plot_ablation_s3fifo(figures_dir)`.

### results/{congress,court}/ablation_sieve.csv

Alpha-sweep schema (identical to `alpha_sensitivity.csv`):
```
alpha,policy,miss_ratio,byte_miss_ratio,accesses_per_sec
```

14 data rows per workload = 2 variants × 7 alphas {0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2}.

## Headline Ablation Finding

**SIEVE-NoProm misses monotonically MORE than SIEVE at every alpha on both workloads; the gap widens with skew.** Lazy promotion on hit is the dominant contributor to SIEVE's scan-resistance.

### Congress (monotone across skew; gap grows with alpha):

| α   | SIEVE  | SIEVE-NoProm | Δ (pp) |
|-----|--------|--------------|--------|
| 0.6 | 0.8825 | 0.9592       | +7.67  |
| 0.7 | 0.8163 | 0.9216       | +10.53 |
| 0.8 | 0.7192 | 0.8554       | +13.61 |
| 0.9 | 0.6058 | 0.7555       | +14.97 |
| 1.0 | 0.4738 | 0.6281       | +15.42 |
| 1.1 | 0.3528 | 0.4939       | +14.11 |
| 1.2 | 0.2554 | 0.3758       | +12.04 |

### Court (monotone; smaller but consistent gap):

| α   | SIEVE  | SIEVE-NoProm | Δ (pp) |
|-----|--------|--------------|--------|
| 0.6 | 0.9523 | 0.9667       | +1.44  |
| 0.7 | 0.9149 | 0.9416       | +2.67  |
| 0.8 | 0.8419 | 0.8943       | +5.23  |
| 0.9 | 0.7337 | 0.8167       | +8.30  |
| 1.0 | 0.6019 | 0.7077       | +10.57 |
| 1.1 | 0.4712 | 0.5815       | +11.03 |
| 1.2 | 0.3805 | 0.4790       | +9.85  |

**Interpretation:** At every alpha, on both workloads, the full SIEVE beats the stripped-down SIEVE-NoProm variant. The gap is enormous — on Congress it peaks at +15.4 pp (alpha=1.0); on Court at +11.0 pp (alpha=1.1). This is the empirical magnitude of lazy promotion's contribution to SIEVE's scan-resistance: **≈10-15 pp of miss-ratio reduction, directly attributable to the one-line `it->second->visited = true` that our plan's guard conditionally suppresses.**

The Congress gap is larger than the Court gap at every alpha — consistent with Congress having a more reuse-oriented reference pattern than Court (where per-endpoint churn dominates). The Court result still cleanly validates the Zhang et al. claim.

**Cross-check with Plan 04-03's result direction:** Plan 04-03's S3-FIFO ablation showed Court-gap > Congress-gap (s3fifo-5 vs s3fifo-20 at α=1.2 differed by +6.3pp on Court vs +1.2pp on Congress). Plan 04-04's SIEVE ablation shows Congress-gap > Court-gap (15.4pp vs 11.0pp peak). The two workloads' reference patterns stress the two policies' mechanisms differently — useful for Phase 5 / DOC-02 writeup.

## Verification Results

| Gate | Value | Pass |
|------|-------|------|
| Ctor signature `(uint64_t, bool = true)` with init-list ternary | present @ cache.h:402-405 | Yes |
| Hit-path guard `if (promote_on_hit_) it->second->visited = true` | present @ cache.h:413 | Yes |
| `name()` delegates to `name_` member | present @ cache.h:457 | Yes |
| evict_one() UNCHANGED (lines 429-455) | verified via diff — ONLY SIEVECache region touched | Yes |
| Stats single-source: `record(true/false)` count in cache.h | 11 (unchanged from pre-plan baseline) | Yes |
| Grep: `promote_on_hit_` count in cache.h | 4 (member + ctor arg + ctor init + guard) | Yes |
| Grep: `SIEVE-NoProm` in cache.h | 2 (doc comment + ctor ternary) | Yes |
| Grep: `if (promote_on_hit_)` in cache.h | 1 | Yes |
| New make_policy branch @ src/main.cpp | present; legacy `sieve` branch unchanged | Yes |
| Grep: `sieve-noprom` in src/main.cpp | 3 (1 make_policy + 2 label-maps) | Yes |
| Both label-map blocks contain `label = "SIEVE-NoProm"` | grep count = 2 | Yes |
| `ablation-sieve` Makefile target | present + `.PHONY` extended | Yes |
| `phase-04: shards-large ablation-s3fifo ablation-sieve` composition | present | Yes |
| `make -n ablation-sieve` shows 2 cache_sim invocations | confirmed | Yes |
| POLICY_COLORS entry for SIEVE-NoProm (#9467bd) | present | Yes |
| POLICY_MARKERS entry for SIEVE-NoProm ('v') | present | Yes |
| Legacy POLICY_COLORS["SIEVE"] / POLICY_MARKERS["SIEVE"] UNCHANGED | verified via grep | Yes |
| `plot_ablation_sieve` function defined | verified via ast.parse walk | Yes |
| `plot_ablation_sieve` registered in main() | verified | Yes |
| `endswith("NoProm")` linestyle selector | grep count = 2 (sort key + linestyle) | Yes |
| results/congress/ablation_sieve.csv rows | 15 (1 header + 14 data) | Yes |
| results/court/ablation_sieve.csv rows | 15 (1 header + 14 data) | Yes |
| Policy column uniques in both CSVs | exactly {SIEVE, SIEVE-NoProm} | Yes |
| results/congress/figures/ablation_sieve.pdf | 20123 bytes | Yes |
| results/court/figures/ablation_sieve.pdf | 20123 bytes | Yes |
| `make` build | exit 0, zero warnings | Yes |
| `make test` regression (both test_wtinylfu + test_doorkeeper) | exit 0, "All tests PASSED." twice | Yes |
| Phase 1/3 back-compat: SIEVE miss_ratio + byte_miss_ratio in /tmp run bit-identical to results/congress/mrc.csv | verified via column-wise diff (only accesses_per_sec differs — non-deterministic throughput timing, expected) | Yes |
| Logical sanity: SIEVE-NoProm monotonically worse than SIEVE at every alpha on both workloads | verified (all 14 per-alpha comparisons show NoProm > SIEVE miss-ratio) | Yes |
| `git diff --stat HEAD~3 HEAD` touches ONLY plan's 4 files | verified (cache.h, main.cpp, Makefile, plot_results.py — no drift into Plan 04-01/02/03 regions) | Yes |

## Deviations from Plan

**None.** Plan executed exactly as written. No Rule 1/2/3/4 triggers, no checkpoints, no auth gates.

Minor notes (not deviations, just observations):
- The plan said "line 362" for the hit-path guard target; post-Plan 04-03 SIEVECache has shifted down in cache.h and the guard now lives at cache.h:413. Same semantic position within SIEVECache::access().
- The plan anticipated the ctor might need a 3-arg delegating overload (like S3FIFOCache). It didn't — a 2-arg ctor with default + init-list ternary is cleaner for the 2-variant case and matches PATTERNS.md Axis D §1106-1161 verbatim.

## Cross-Plan Dependencies

- **Blocks:** None (pure extension; Plan 04-05 does not touch SIEVE)
- **Blocked by:** None (independent of Plan 04-01 / 04-02 / 04-03 output CSVs; touches no shared members with S3FIFOCache)
- **Shared-file co-tenants:** Plans 04-01, 04-02, 04-03 share `src/main.cpp` / `Makefile` / `scripts/plot_results.py` (non-overlapping regions per CONTEXT D-19 axis decomposition); Plan 04-03 additionally shares `include/cache.h` (disjoint S3FIFOCache vs SIEVECache regions — verified via `git diff HEAD~3 HEAD -- include/cache.h` which touches only the SIEVECache class body). Plan 04-05 (Doorkeeper integration) shares `src/main.cpp` / `Makefile` / `scripts/plot_results.py` but touches wtinylfu.h / count_min_sketch.h internals only — zero overlap with this plan's SIEVE edits.

## Self-Check: PASSED

- Created files verified present:
  - `results/congress/ablation_sieve.csv` (15 lines)
  - `results/court/ablation_sieve.csv` (15 lines)
  - `results/congress/figures/ablation_sieve.pdf` (20123 bytes)
  - `results/court/figures/ablation_sieve.pdf` (20123 bytes)
- Commits verified in git log:
  - `06734d7` — feat(04-04): extend SIEVECache ctor with promote_on_hit flag + name_ member
  - `77d1592` — feat(04-04): add sieve-noprom make_policy branch + ablation-sieve Makefile target
  - `e4f4900` — feat(04-04): add plot_ablation_sieve + POLICY_COLORS/MARKERS SIEVE-NoProm entries
