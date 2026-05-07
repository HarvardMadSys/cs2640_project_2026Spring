---
phase: 06-writeup-demo
plan: 05
subsystem: docs
tags: [latex, paper-writeup, doc-02, mechanism-analysis, reproducibility-appendix]

# Dependency graph
requires:
  - phase: 06-01
    provides: docs/DOC-02-final-report.tex scaffold with \input{sections/05-mechanism}, \input{sections/08-byte-mrc}, \input{sections/09-conclusion}, and \appendix + \input{sections/10-appendix} slots
  - phase: 05-cross-workload-analysis-infrastructure
    provides: 05-ANALYSIS.md prose source (lines 98-215, 371-445) + results/compare/winner_per_regime.md + aggregated α-sensitivity numbers
provides:
  - docs/sections/05-mechanism.tex — D-03 centerpiece (SIEVE-ties-W-TinyLFU-on-Congress mechanism)
  - docs/sections/08-byte-mrc.tex — σ=0.21 methodological warning for heavy-tailed byte-MRC
  - docs/sections/09-conclusion.tex — D-05 practitioner decision tree + V2-01..V2-06 limitations
  - docs/sections/10-appendix.tex — D-11 reproducibility appendix (10 figures, per revision W-3)
affects: [06-06-phase-verification, paper-final-build (make paper target)]

# Tech tracking
tech-stack:
  added: []  # No new deps — LaTeX already in place per 06-01
  patterns:
    - "Forward/backward cross-referencing via \\ref{} + \\label{} across sections (figures, tables, section labels)"
    - "Quoted paper-claim block (verbatim from 05-ANALYSIS.md) as rhetorical device in mechanism & byte-MRC sections"
    - "Appendix-as-figure-dump structure (\\subsection per figure + short caption) per D-11"

key-files:
  created:
    - docs/sections/05-mechanism.tex
    - docs/sections/08-byte-mrc.tex
    - docs/sections/09-conclusion.tex
    - docs/sections/10-appendix.tex
  modified: []

key-decisions:
  - "§5 per-α gap table verbatim from 05-ANALYSIS.md lines 106-120 (no data re-interpretation)"
  - "§5 α ∈ {1.3, 1.4, 1.5} addendum explicitly labeled 'single-seed' per threat T-06-05-01 mitigation"
  - "§9 Mixed Sizes row explicitly hedged as 'single-seed' per winner_per_regime.md footnote"
  - "§10 appendix does NOT re-declare \\appendix (parent DOC-02-final-report.tex owns that marker)"
  - "§10 figure count pinned to the 10 mandated figures; optional hit_ratio_vs_cachesize.pdf files do not exist on disk, so no extras added"

patterns-established:
  - "Mechanism-section template: data table → hypothesized mechanism → paper-claim quote → narrative implication (applied in §5 and §8)"
  - "Decision-tree template in conclusion: itemized α_raw/size/cache_frac rules + limitations subsection (applied in §9)"

requirements-completed: [DOC-02]

# Metrics
duration: ~35min
completed: 2026-04-22
---

# Phase 6 Plan 05: Four closing sections — mechanism deep-dive, byte-MRC warning, practitioner decision tree, reproducibility appendix

**Authored the paper's D-03 mechanism centerpiece (§5), D-12 byte-MRC methodological warning (§8), D-05 practitioner decision-tree conclusion (§9), and D-11 reproducibility appendix (§10 with 10 figures per revision W-3) — 2,747 words across 4 files, completing the Wave-2 half of DOC-02 body content.**

## Performance

- **Duration:** ~35 min (all 4 tasks sequential, no deviations)
- **Started:** 2026-04-22T01:30:00Z (approx)
- **Completed:** 2026-04-22T02:05:13Z
- **Tasks:** 4
- **Files modified:** 4 created, 0 modified

## Accomplishments

- §5 **Mechanism Deep-Dive** (887 words) — the D-03 centerpiece. Per-α gap table (Congress vs Court) verbatim from 05-ANALYSIS.md; SIEVE-visited-bit-as-admission-filter argument; Congress α_raw=0.231 vs Court α_raw=1.028 contrast; single-seed α ∈ {1.3, 1.4, 1.5} addendum with explicit hedge; closing narrative on the TinyLFU-paper assumption and Caffeine production decision.
- §8 **Byte-MRC Warning** (564 words) — σ=0.207 table for Court at cache_frac=0.01; 462KB / 1,381B = 335× outlier mechanism; 77%-of-cache single-outlier argument; paper claim quoted verbatim; CI-band practitioner takeaway.
- §9 **Conclusion** (752 words) — `winner_per_regime_bar.pdf` opener; 4-row regime table (with Mixed Sizes hedged as single-seed); D-05 practitioner decision tree as 4-item flowchart; Limitations subsection naming all six V2-01..V2-06 deferred items.
- §10 **Appendix** (544 words, 127 lines) — 10 `\subsection + \includegraphics` blocks per D-11 / revision W-3; per-workload MRC/byte-MRC/OHW (6 figures) + SHARDS error + convergence (2) + cross-workload policy-delta + MRC overlay (2) = exactly 10. Does NOT re-declare `\appendix` (parent DOC-02 owns that).

## Task Commits

Each task was committed atomically via `git commit --no-verify` (parallel-executor protocol):

1. **Task 1: §5 mechanism deep-dive (D-03 centerpiece)** — `910444d` (feat)
2. **Task 2: §8 byte-MRC methodological warning** — `f54b85c` (feat)
3. **Task 3: §9 conclusion with D-05 decision tree + V2 limitations** — `ddd3e6e` (feat)
4. **Task 4: §10 reproducibility appendix (D-11 / revision W-3)** — `f715668` (feat)

## Files Created/Modified

- `docs/sections/05-mechanism.tex` (119 lines, 887 words) — D-03 mechanism deep-dive
- `docs/sections/08-byte-mrc.tex` (86 lines, 564 words) — D-12 byte-MRC σ=0.21 warning
- `docs/sections/09-conclusion.tex` (119 lines, 752 words) — D-05 conclusion with decision tree + V2 limitations
- `docs/sections/10-appendix.tex` (127 lines, 544 words, 10 figures) — D-11 reproducibility appendix

**Per-section word/line counts:**

| File | Lines | Words | Figures | Tables |
|------|-------|-------|---------|--------|
| 05-mechanism.tex | 119 | 887 | 0 | 1 (tab:mechanism_gap) |
| 08-byte-mrc.tex | 86 | 564 | 0 | 1 (tab:byte_mrc_variance) |
| 09-conclusion.tex | 119 | 752 | 1 (fig:winner_bar) | 1 (tab:winner_regime) |
| 10-appendix.tex | 127 | 544 | 10 (fig:app_*) | 0 |
| **total** | **451** | **2747** | **11** | **3** |

## Verbatim-source confirmations

- **§5 per-α gap table** matches 05-ANALYSIS.md lines 106-120 **verbatim**. Congress values (+0.0020, -0.0002, +0.0014, -0.0004, -0.0047, -0.0019, -0.0016) and Court values (+0.0418, +0.0492, +0.0543, +0.0540, +0.0469, +0.0393, +0.0359) are copied exactly. Asterisks on SIEVE-wins rows (α ∈ {1.0, 1.1, 1.2} on Congress) match the source's ← SIEVE wins annotations.
- **§5 addendum α ∈ {1.3, 1.4, 1.5}** data is labeled "single-seed" in both the prose ("We extended the Congress α grid to {1.3, 1.4, 1.5} with a single-seed run") and the mechanism quote block — matches 05-ANALYSIS.md lines 397-445 hedging and threat T-06-05-01 mitigation requirement.
- **§8 byte-miss σ table** (W-TinyLFU 0.611 ± 0.207, SIEVE 0.656 ± 0.205, LRU 0.755 ± 0.171) matches 05-ANALYSIS.md lines 179-183 verbatim.
- **§9 regime-winner table** matches results/compare/winner_per_regime.md **verbatim**: Small Cache W-TinyLFU 0.869/0.831; High Skew SIEVE 0.351 / W-TinyLFU 0.386; Mixed Sizes N/A / W-TinyLFU 0.284 (with single-seed hedge); OHW W-TinyLFU 0.712/0.728.

## Appendix figure count

**Exactly 10 `\includegraphics` blocks** (confirmed via `grep -c '\includegraphics' docs/sections/10-appendix.tex` = 10). The plan's optional "figures 11-12" (`hit_ratio_vs_cachesize.pdf` for congress and court) were **not included** because `ls results/{congress,court}/figures/hit_ratio_vs_cachesize.pdf` returned "missing" for both. D-11's 10-12 target was therefore met at the 10-minimum.

All 10 figure paths verified to exist on disk **before** the appendix commit:
- `results/congress/figures/{mrc,byte_mrc,ohw}.pdf` (3)
- `results/court/figures/{mrc,byte_mrc,ohw}.pdf` (3)
- `results/shards_large/figures/{shards_error,shards_convergence}.pdf` (2)
- `results/compare/figures/{compare_policy_delta,compare_mrc_overlay}.pdf` (2)

No figure paths required substitution.

## Decisions Made

- **10 (not 12) appendix figures.** The optional `hit_ratio_vs_cachesize.pdf` files do not exist on disk. Rather than regenerate them (out of Phase 6 scope per 06-PATTERNS.md constraint 2) or reference non-existent files (blocked by threat T-06-05-03 mitigation), the appendix stops at the 10 mandated figures. 10 is within the D-11 "10-12" range.
- **§10 section-level label is `sec:appendix`** rather than reusing the root-level `\appendix` declaration name. This permits other sections to `\ref{sec:appendix}` if needed.
- **Paper-claim quote blocks** used verbatim in §5 and §8 (italicized small-text `quote` environments) as a rhetorical device to distinguish the formal paper-voice claim from the prose analysis — lets the reader extract the one-sentence takeaway without re-parsing the argument.
- **Threat T-06-05-01 mitigation** applied strictly: every data point in §5's per-α table was cross-checked against 05-ANALYSIS.md lines 106-120 before commit; the single-seed α extension is labeled as such in both prose and the addendum quote block.

## Deviations from Plan

None — plan executed exactly as written. All 4 tasks met every grep-based acceptance criterion on the first commit; no auto-fixes, no architectural changes, no authentication gates.

## Issues Encountered

- **Worktree base mismatch at startup.** Worktree branch `worktree-agent-a60ca9a4` was created from commit `44f13ea` (pre-Phase-6), but the plan expected base `0aaa886` (post-06-03 merge) so that the 06-01 LaTeX scaffold would be present. Resolved by `git reset --hard 0aaa886` per the worktree_branch_check protocol. No data loss — the worktree had no in-flight changes.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- **4 section files committed** on worktree branch `worktree-agent-a60ca9a4`; ready for parallel-merge with Plan 06-04 (which writes the disjoint file set `{02-related,03-workload,04-policies,06-shards,07-ablations}`).
- **DOC-02 body sections** are now complete pending 06-04: after both worktrees merge, all 11 `\input{sections/...}` slots in `docs/DOC-02-final-report.tex` will resolve.
- **Plan 06-06 (phase verification)** can run `make paper` as its end-to-end smoke check once the parallel merge completes. Every cross-reference produced in this plan (`\ref{tab:workload}`, `\ref{fig:opening}`, `\ref{fig:workload_viz}`, `\ref{fig:alpha_sens}`, `\ref{fig:shards_overlay}`, `\ref{sec:shards}`, `\ref{sec:workload}`, `\ref{sec:policies}`, `\ref{sec:ablations}`, `\ref{tab:alpha_sens}`) relies on Plan 06-04 defining those labels.
- **No blockers.** Plan 06-06 verifier should focus on: (a) confirming all cross-section `\ref{}` targets resolve without `??` in the final PDF, (b) confirming §10 appendix renders after `\appendix` without re-declaration errors, (c) confirming all 10 appendix `\includegraphics` paths resolve at build time.

## Self-Check: PASSED

Verified before writing this line:

- [x] `docs/sections/05-mechanism.tex` exists (119 lines, 887 words, 3 citations, tab:mechanism_gap)
- [x] `docs/sections/08-byte-mrc.tex` exists (86 lines, 564 words, tab:byte_mrc_variance)
- [x] `docs/sections/09-conclusion.tex` exists (119 lines, 752 words, 10 V2/decision-tree references, fig:winner_bar, tab:winner_regime)
- [x] `docs/sections/10-appendix.tex` exists (127 lines, 544 words, 10 includegraphics, 10 subsections, no re-declared \appendix)
- [x] Commit `910444d` found in `git log` (Task 1)
- [x] Commit `f54b85c` found in `git log` (Task 2)
- [x] Commit `ddd3e6e` found in `git log` (Task 3)
- [x] Commit `f715668` found in `git log` (Task 4)
- [x] STATE.md NOT modified (per parallel-executor protocol)
- [x] ROADMAP.md NOT modified (per parallel-executor protocol)

---
*Phase: 06-writeup-demo*
*Completed: 2026-04-22*
