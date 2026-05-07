---
phase: 06-writeup-demo
verified: 2026-04-22T17:00:00Z
status: passed
score: 4/4 must-haves verified
overrides_applied: 0
requirements_coverage:
  - id: DOC-02
    status: satisfied
  - id: DOC-03
    status: satisfied
  - id: DOC-04
    status: satisfied
---

# Phase 6: Writeup & Demo — Verification Report

**Phase Goal:** The three final deliverables the professor evaluates — class-report paper, AI-use report (specifically requested), and a live <60s demo that actually works on the target laptop.
**Verified:** 2026-04-22
**Status:** PASS
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths (Success Criteria from ROADMAP §Phase 6)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| SC-1 | DOC-02 final report exists as a submission-ready PDF with: workload char table up front, policy comparison (both workloads, full MRC + error bands), SHARDS validation section with 4-rate table, ablation figures, winner-per-regime + practitioner decision tree at end | PASS | 20-page LaTeX-compiled PDF at `docs/DOC-02-final-report.pdf` (681 KB, pdfTeX-1.40.25); all 8 required elements present and located in correct positions. See detail below. |
| SC-2 | DOC-03 AI-use report exists and includes the PROCESS.md bug list framed as concrete AI-collaboration learning moments (not successes-only) | PASS | `docs/DOC-03-ai-use-report.md` (3,566 words) — decision-log format per D-06; audited bug count per D-07 explicitly stated as "9 (PROCESS.md body) + 12 (Phase 5 review) = 21"; full "What Did Not Work — Learning Moments" section (§ line 109) with 5 named failure patterns; D-09 GSD meta-layer covered; D-10 research-phase given equal weight to implementation. |
| SC-3 | `demo.sh` exists, sources `.env`, sets `DYLD_LIBRARY_PATH`, runs a pre-loaded <10K-access trace through the simulator in <60s total wall-clock, and has been tested end-to-end at least 3 times on the target demo laptop | PASS | `demo.sh` 61 LOC `bash -n` clean; `source .env` at L18, `export DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib` at L24; trace is `traces/demo_trace.csv` (5,001 lines = 5K rows + header, well under 10K); `demo-rehearsal.log` (252 lines, 12.5 KB) contains 3 `=== Rehearsal` markers (lines 2, 86, 170) all with `[verdict] PASS`; wall-clocks 4s / 3s / 4s (all « 60s). |
| SC-4 | A screen-recording backup of the full working demo exists and is ready to cut to if the live run hangs | PASS | `docs/demo-backup.mov` 4,376,991 bytes (4.2 MB), `file` identifies as "ISO Media, Apple QuickTime movie, Apple QuickTime (.MOV/QT)"; non-empty and valid video container. |

**Score:** 4/4 truths verified

---

## SC-1 Detail: DOC-02 Structural Element Checklist

Checked via `pdftotext -layout docs/DOC-02-final-report.pdf` (1,124 text lines across 20 pages, 20 figures, 8 tables).

| Required Element (per ROADMAP SC-1 + CONTEXT D-01..D-14) | Status | Evidence |
|---|---|---|
| **Workload characterization TABLE up front** | PASS | Table 1 at PDF §3 line 131–140 — 10 rows (Total requests, Unique objects, Zipf α MLE, OHW ratio, Mean/Median/p95/Max size, Working set bytes) × 2 columns (Congress, Court). Exact 10-row shape per ANAL-03. |
| **Policy comparison for BOTH workloads** | PASS | §4 "Policy Comparison" with headline result "W-TinyLFU wins 11 of 12 miss-ratio cells (6 cache fractions × 2 workloads)"; Figure 1 (opening hook) + Figure 3 (alpha sensitivity) explicitly per-workload. |
| **Full MRC + error bands (±1σ across 5 seeds)** | PASS | Figure 1 caption: "Shaded bands are ±1σ across 5 seeds"; compiled from `results/compare/figures/compare_mrc_2panel.pdf` (25,936 B) per D-04. Welch's t p-values cited at PDF line 286: p∈[1.2e-06, 8.7e-08]. |
| **SHARDS validation SECTION** | PASS | §6 "SHARDS Validation" at PDF line 392, explicit self-convergence methodology, 50K-oracle complementary regime. |
| **4-rate table in SHARDS section** | PASS | Table 3 at PDF line 424 with 4 sampled rates {0.01%, 0.1%, 1%, 10%}; 10% is the reference, table shows 3 compared-vs-reference rows (0.01% flagged below 200-sample floor, 0.1% and 1% clean); methodology line 401 explicitly names "four sampling rates — {0.01%, 0.1%, 1%, 10%}". |
| **All 3 ablation figures (S3-FIFO, SIEVE, Doorkeeper) per D-14** | PASS | §7 "Ablations" hosts all 3: Figure 5 S3-FIFO small-queue ratio (5/10/20), Figure 6 SIEVE visited-bit (promote_on_hit on/off), Figure 7 W-TinyLFU ± Doorkeeper. All three with per-workload Congress│Court panels. |
| **Winner-per-regime figure (winner_per_regime_bar.pdf embedded)** | PASS | Figure 8 at PDF line 702 "Winner per Regime (D-01 regime definitions; 5-seed mean)" — source file exists at `results/compare/figures/winner_per_regime_bar.pdf` (21,950 B). Matching Table 5 with named regime winners (Small Cache, High Skew, Mixed Sizes, OHW Regime) × 2 workloads. |
| **Practitioner DECISION TREE at end (D-05)** | PASS | §9.2 "A practitioner decision tree" at PDF line 745 with 4 enumerated rules keyed on raw α thresholds and size-outlier presence. Mirrors D-05 specification verbatim. |
| **Appendix with 10-12 reproducibility figures (D-11)** | PASS | Appendix §A.1–A.10 (lines 811–852) — 10 sub-sections with per-workload MRC/byte-MRC/OHW/SHARDS/delta/overlay figures; confirmed 10 appendix figure slots matching D-11 target. |

**Requirements closure:** DOC-02 satisfied across plans 06-01 (skeleton), 06-04 (breadth body), 06-05 (depth body), 06-06 (final build + README).

---

## SC-2 Detail: DOC-03 Content Checklist

| Content Requirement (from CONTEXT.md D-06..D-10) | Status | Evidence |
|---|---|---|
| Decision-log format per D-06 | PASS | Opening hook (§"A Concrete Decision Moment") is a concrete "Claude suggested X, I caught Y" moment — literal match to D-06 specified format and the CONTEXT `specifics` opening-hook candidate. All 5 body sections use the "Claude did X, I did Y because Z" pattern. |
| Audited PROCESS.md bug count — not pinned to "9" per D-07 | PASS | §"Bugs Found and Fixed — Audited Count" explicitly states "**PROCESS.md body total: 9 bugs.** (4 Round 1 + 4 Round 2 + 1 SHARDS denominator.)" followed by "**Total audited count across all sources: 9 + 12 = 21 issues.**" Literal D-07 satisfaction: re-audit confirmed body count as 9, added 12 Phase 5 findings. |
| D-08 honest-but-careful framing of failures | PASS | §"What Did Not Work — Learning Moments" enumerates 5 named failure patterns (over-trusting confident wrong answers, bugs Claude shipped review caught, planner-misread-source, environment issues invisible to AI, plan specificity pays). Framing is explicitly "what instinct to develop, not what to blame" (line 111). |
| D-09 Claude Code + GSD meta-layer scope | PASS | Explicit three-layer structure at §"How This Project Was Built": (1) Research-phase decisions, (2) GSD planning and orchestration, (3) Claude Code implementation partnership. Dedicated section for each. |
| D-10 research-phase decisions weighted equally with implementation | PASS | §"Research-Phase Decisions" covers 4 pre-code decisions (CourtListener over PACER, W-TinyLFU selection, replay-Zipf, SHARDS 1M self-convergence) in the same depth as §"Implementation Partnership"; no sanitized successes-only tone (explicit PACER paywall disqualification etc.). |

**Requirements closure:** DOC-03 satisfied by plan 06-03.

---

## SC-3 + SC-4 Detail: Demo Readiness Checklist

| Test | Command | Result |
|---|---|---|
| `demo.sh` exists and is executable | `test -x demo.sh` | PASS (2402 B, mode 755) |
| `bash -n demo.sh` (syntax) | `bash -n /Users/mirayu/civicache/demo.sh` | PASS (no errors) |
| `demo.sh` sources `.env` | `grep -n 'source .env' demo.sh` | PASS — line 18 `source .env` inside `if [[ -f .env ]]` guard |
| `demo.sh` sets `DYLD_LIBRARY_PATH` | `grep -n 'DYLD_LIBRARY_PATH' demo.sh` | PASS — line 24 `export DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib` |
| Demo trace is <10K accesses | `wc -l traces/demo_trace.csv` | PASS — 5,001 lines (5K rows + header) per D-19 |
| 3+ rehearsals logged | `grep -c '^=== Rehearsal ' demo-rehearsal.log` | PASS — 3 rehearsals (lines 2, 86, 170) |
| All rehearsals pass | `grep -c '^\[verdict\]     PASS$' demo-rehearsal.log` | PASS — 3 PASS verdicts |
| Each rehearsal <60s wall-clock | `grep 'wall-clock:' demo-rehearsal.log` | PASS — 4s, 3s, 4s (all « 60s budget) |
| Screen recording exists + non-empty | `test -s docs/demo-backup.mov` | PASS — 4,376,991 B (4.2 MB) |
| Screen recording is valid video | `file docs/demo-backup.mov` | PASS — "ISO Media, Apple QuickTime movie, Apple QuickTime (.MOV/QT)" |

**Requirements closure:** DOC-04 satisfied across plans 06-02 (demo.sh + demo_trace.csv) and 06-07 (3 rehearsals + screen recording).

---

## Cross-Plan Coherence

| Check | Result |
|---|---|
| No unresolved LaTeX refs in PDF | PASS — `grep -c '??' /tmp/doc02.txt` = 0 (Plan 06-06 fixed 4 undefined refs per its SUMMARY) |
| Figure/Table counts match claims | PASS — 20 figures, 8 tables extracted; matches plans 06-04/05/06 |
| All 4 aggregated cross-workload figures present | PASS — `results/compare/figures/{compare_mrc_2panel,compare_mrc_overlay,compare_policy_delta,winner_per_regime_bar}.pdf` all exist |
| Workload-characterization + winner-per-regime tables sourced from Phase 5 artifacts | PASS — `results/compare/{workload_characterization,winner_per_regime}.{md,json}` exist; Table 1 + Table 5 in PDF match the 10-row / 4-regime content from Phase 5 |
| README.md Phase 6 additions | PASS — W-TinyLFU row at line 16; live-demo section line 47-51; docs/ in tree line 137; DOC-04 reference line 146; Einziger + Caffeine citations lines 154-155 |

---

## Requirements Coverage

| Requirement | Source Plan(s) | Description | Status | Evidence |
|---|---|---|---|---|
| **DOC-02** | 06-01, 06-04, 06-05, 06-06 | Final report (class-report length) — workload char table up front, policy comparison, SHARDS validation, ablations, winner-per-regime, practitioner decision tree | SATISFIED | 20-page PDF at `docs/DOC-02-final-report.pdf`; all 9 SC-1 structural elements verified (see SC-1 detail table) |
| **DOC-03** | 06-03 | AI-use report — what worked/didn't with Claude Code, including PROCESS.md bug list as concrete learning moments | SATISFIED | 3,566-word `docs/DOC-03-ai-use-report.md`; all 5 D-06..D-10 checks PASS (see SC-2 detail table) |
| **DOC-04** | 06-02, 06-07 | Live simulator demo script `demo.sh` — <60s runtime, pre-loaded <10K trace, screen-recording backup, tested 3+ times on target laptop | SATISFIED | demo.sh + traces/demo_trace.csv + demo-rehearsal.log (3 rehearsals, all PASS) + docs/demo-backup.mov (see SC-3/SC-4 detail table) |

**No orphaned requirements** — REQUIREMENTS.md §Traceability maps exactly DOC-02/03/04 to Phase 6, all three appear in at least one plan's `requirements` field.

---

## Anti-Pattern Scan

Per the phase verification context, standard code-level checks are intentionally skipped (no `src/` changes — this is a docs-only phase). Anti-pattern scan focused on docs/demo artifacts:

| File | Pattern | Severity | Finding |
|---|---|---|---|
| `demo.sh` | TODO/FIXME/placeholder | None | Clean — no deferred work indicators |
| `docs/DOC-03-ai-use-report.md` | TODO/FIXME/placeholder | None | Clean |
| `demo-rehearsal.log` L2 | "Rehearsal N" (template placeholder not substituted to "Rehearsal 1") | INFO | Cosmetic — count-based verifier (`grep -c '^=== Rehearsal '`) still returns 3; verdicts and wall-clocks for this rehearsal still pass. Non-blocking. |

---

## Deviations from Locked Decisions

None. All 19 decisions (D-01..D-19) surveyed against the delivered artifacts:

| Decision | Deliverable | Status |
|---|---|---|
| D-01 (lead with surprise finding) | Abstract + §1 open with "SIEVE ≈ W-TinyLFU on Congress vs. +4-5pp on Court" | Honored |
| D-02 (hybrid tone) | DOC-02 §4 third-person results + §3 first-person methodology; DOC-03 first-person throughout | Honored |
| D-03 (one deep finding + breadth elsewhere) | §5 SIEVE≈W-TinyLFU deep-dive with per-α gap Table 2 + addendum; other findings paragraph-length | Honored |
| D-04 (figure-led opening page 1) | Figure 1 compare_mrc_2panel.pdf at top of page 1 with callout | Honored |
| D-05 (practitioner decision tree in conclusion) | §9.2 with 4 enumerated rules | Honored |
| D-06 (decision-log format DOC-03) | Opening hook + Research/GSD/Implementation sections all decision-keyed | Honored |
| D-07 (actual bug count, not pinned to 9) | "9 body + 12 Phase 5 = 21 total" explicitly surfaced | Honored |
| D-08 (balanced honesty on failures) | §"What Did Not Work" has 5 named failure patterns framed as learning moments | Honored |
| D-09 (Claude Code + GSD meta-layer scope) | Three-layer section structure in DOC-03 | Honored |
| D-10 (research-phase equal weight) | Dedicated "Research-Phase Decisions" § with 4 pre-code decisions | Honored |
| D-11 (6-8 main + 10-12 appendix) | Main body ~8 figures (Fig 1–8); appendix A.1–A.10 with 10 slots | Honored |
| D-12 (must-have main-body figures) | compare_mrc_2panel = Fig 1; winner_per_regime_bar = Fig 8; shards_mrc_overlay embedded in Fig 4 right panel | Honored |
| D-13 (2nd-tier must-have figures) | alpha_sensitivity = Fig 3; ablation_doorkeeper = Fig 7; workload = Fig 2 | Honored |
| D-14 (ablations section with all 3) | §7 hosts all 3 with Figs 5/6/7 | Honored |
| D-15 (live 6-policy sweep on small trace) | demo.sh runs 6 policies at 4 cache sizes; live miss-ratio table via `column -t -s,` | Honored |
| D-16 (single full-demo screen recording) | docs/demo-backup.mov (4.2 MB, QuickTime .mov) | Honored |
| D-17 (demo.sh self-sources .env) | L18 `source .env` inside existence guard; L24 `export DYLD_LIBRARY_PATH` | Honored |
| D-18 (3 rehearsals logged) | demo-rehearsal.log has 3 markers + 3 PASS verdicts | Honored |
| D-19 (~5K-request pre-loaded demo trace) | traces/demo_trace.csv = 5,001 lines (5K + header) per D-19 | Honored |

---

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|---|---|---|---|
| demo.sh is syntactically valid bash | `bash -n /Users/mirayu/civicache/demo.sh` | exit 0, no output | PASS |
| PDF is readable by pdftotext | `pdftotext -layout docs/DOC-02-final-report.pdf -` | 1,124 text lines extracted | PASS |
| PDF has >8 pages (06-06 SUMMARY gate) | `pdfinfo docs/DOC-02-final-report.pdf` | Pages: 20 | PASS |
| Screen recording is valid media container | `file docs/demo-backup.mov` | ISO Media, Apple QuickTime | PASS |
| All 3 demo rehearsals produce consistent numbers | Compared miss-ratio tables across Rehearsals N/2/3 | Identical across all 3 (deterministic under D-19 pre-loaded trace) | PASS |

---

## Human Verification Required

None.

All four success criteria are fully programmatically verifiable for this phase:
- SC-1: structural existence of elements in the PDF — verified via pdftotext content extraction
- SC-2: content structure of the markdown report — verified via section headers and explicit D-key coverage
- SC-3: demo.sh + trace + rehearsal log — verified via file tests, grep, and wall-clock extraction
- SC-4: screen recording — verified via file size + `file` magic-number identification

Note: the *aesthetic quality* of the PDF (typography, figure composition, narrative flow) and the *demo delivery* (speaking cadence, narration) would in principle benefit from human review, but none of these affect SC-1..4 satisfaction. The deliverables meet the contract as written.

---

## Phase Verdict

**PASS — 4/4 Success Criteria satisfied, 3/3 Requirements (DOC-02, DOC-03, DOC-04) satisfied, all 19 locked decisions (D-01..D-19) honored.**

The three final submission artifacts — 20-page final report PDF, 3,566-word AI-use report with audited 21-issue bug log, and live 6-policy demo with 3-rehearsal evidence + video backup — are complete and goal-achieving. No gaps, no deferred items, no overrides, no anti-patterns above INFO severity (the single INFO is a cosmetic "Rehearsal N" template-placeholder that does not break the regex-based verifier).

Phase 6 — and Milestone 2 in aggregate (6/6 phases, 29/29 requirements) — is submission-ready.

---

*Verified: 2026-04-22*
*Verifier: Claude (gsd-verifier)*
