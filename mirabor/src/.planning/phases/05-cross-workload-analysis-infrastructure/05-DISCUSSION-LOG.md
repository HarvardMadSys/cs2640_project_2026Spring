# Phase 5: Cross-Workload Analysis Infrastructure — Discussion Log

**Date:** 2026-04-20
**Mode:** Interactive (default; no --auto, --all, --chain, --power)
**User:** mirabor (varunrahel@gmail.com)

This is the full audit trail of Q&A during `/gsd-discuss-phase 5`. For human reference only (software audits, compliance). Downstream agents (researcher, planner, executor) read `05-CONTEXT.md`, not this file.

---

## Gray area selection (multiSelect)

**Question:** Phase 5 is analysis/scripts only (no new C++). Which of these decisions do you want to discuss before planning?

**Options presented:**
1. Regime definitions for winner-per-regime — What concrete thresholds define 'small cache' / 'high skew' / 'mixed sizes' / 'OHW regime'?
2. Statistical test for 'n.s.' flagging — literal 2σ vs Welch's t-test vs bootstrap
3. Cross-workload plot design — side-by-side vs overlay vs delta vs bar
4. Multi-seed sweep scope — full grid vs targeted subset

**User selection:** ALL FOUR

---

## Area 1 — Regime definitions for winner-per-regime

### Q1.1: What cache fraction(s) define the 'small cache' regime?

**Options:**
- 0.1% only (smallest) — Recommended
- ≤0.5% (two smallest)
- ≤1% (three smallest)

**User answer:** 0.1% only (smallest)

### Q1.2: What alpha(s) define the 'high skew' regime?

**Options:**
- α ≥ 1.0 (three rows) — Recommended
- α ≥ 0.8 (five rows)
- α = 1.2 only (peak)

**User answer:** α ≥ 1.0 (three rows)

### Q1.3: How should 'mixed sizes' regime be defined for byte-MRC analysis?

**Options:**
- Byte-MRC on Court only (empirical) — Recommended
- p95/median size ratio ≥ threshold
- Drop 'mixed sizes' as its own regime

**User answer:** Byte-MRC on Court only (empirical)

### Q1.4: How should 'OHW regime' be defined?

**Options:**
- Use empirical OHW ratios from workload_stats — Recommended
- OHW ratio > 0.3 threshold
- Short-window OHW ratio from one_hit_wonder.csv

**User answer:** Use empirical OHW ratios from workload_stats

### Notes

All four answers took the recommended option. The unifying pattern is "let the workloads and the existing sweep grids define the regimes" — no arbitrary thresholds to defend in DOC-02 + every cell reuses an existing sweep column.

---

## Area 2 — Statistical test for 'n.s.' flagging

### Q2.1: Which statistical test for flagging policy-A-vs-policy-B differences as 'not significant'?

**Options:**
- Literal 2σ-of-seed-std (simple) — Recommended (per ROADMAP literal)
- Welch's t-test (p<0.05)
- Bootstrap 95% CI on Δ
- Both literal 2σ + Welch's t-test

**User answer:** Welch's t-test (p<0.05)

### Q2.2: What should the per-cell CI plot band show on cross-workload plots?

**Options:**
- Mean ± 1σ — Recommended (ROADMAP-literal)
- Mean ± 2σ
- Min/max envelope + mean line
- 5 individual thin lines + mean bold

**User answer:** Mean ± 1σ

### Notes

User chose the rigor upgrade for table claims (Welch's t-test for `n.s.` column) but kept the ROADMAP-literal ±1σ for plot bands. This matches a sensible "formal where formal matters, visual where visual matters" split.

---

## Area 3 — Cross-workload plot design

### Q3.1: Which cross-workload plot(s) should ship in results/compare/figures/? (multiSelect)

**Options:**
- Side-by-side 2-panel (Congress | Court) — Recommended baseline
- Policy-Δ plot (Court miss_ratio − Congress miss_ratio)
- Overlay (solid = Congress, dashed = Court, same axes)
- Per-regime bar chart (winner-per-regime visual)

**User answer:** ALL FOUR

### Q3.2: For the 5-seed CI plot: which is the canonical 'final policy comparison' figure for DOC-02?

**Options:**
- 2-panel MRC with CI bands (Congress | Court) — Recommended
- 2-panel alpha-sensitivity with CI bands (Congress | Court)
- Both — MRC and alpha-sensitivity, 4 panels total

**User answer:** 2-panel MRC with CI bands (Congress | Court)

### Notes

User wanted all four cross-workload views shipped, which is more figures than a typical class report uses. Noted in CONTEXT.md that the bar chart is the most likely omission if Phase 6's page budget binds (its story is also in the markdown table).

---

## Area 4 — Multi-seed sweep scope

### Q4.1: How much of the sweep gets 5-seed coverage?

**Options:**
- Full grid (all sweeps × 5 seeds) — Recommended
- MRC grid only (not alpha sensitivity)
- Winner-per-regime cells only

**User answer:** Full grid (all sweeps × 5 seeds)

### Q4.2: Which 5 seeds should the multi-seed runs use?

**Options:**
- {42, 7, 13, 23, 31} — Recommended
- FNV-based: {42, SEED_A, SEED_B, SEED_C, SEED_D}
- {1, 2, 3, 4, 5}

**User answer:** {42, 7, 13, 23, 31}

### Q4.3: Where does the seed enter the simulator?

**Options:**
- New --seed CLI flag on cache_sim — Recommended
- Python wrapper regenerates traces with different seeds externally
- Env var (SEED=N ./cache_sim ...)

**User answer:** New --seed CLI flag on cache_sim

### Notes

Chose full coverage + a minimal C++ edit. The 1-line argparse addition threads into two places (generate_zipf_trace + SHARDS sampler). Must preserve Phase 1-4 bit-identical back-compat when --seed is absent (default=42 code path unchanged).

Estimated 780 sim cells × ~1-2 sec/cell = 10-20 min wall-clock — feasible on the target laptop.

---

## Wrap-up

**Question:** Ready to write CONTEXT.md, or explore more gray areas first?

**Options:**
- Create context — Recommended
- Explore more gray areas (e.g., decision-tree structure, output-file naming, LaTeX pasteability, CI regression gate)

**User answer:** Create context
