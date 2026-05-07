# Phase 6: Writeup & Demo - Context

**Gathered:** 2026-04-21
**Status:** Ready for planning

<domain>
## Phase Boundary

Deliver the three final submission artifacts the professor evaluates:
- **DOC-02:** Class-report paper — submission-ready PDF (or markdown pipeline producing one). Front-loads the surprise mechanism finding; ends on the practitioner decision tree.
- **DOC-03:** AI-use report — decision-log format covering Claude Code collaboration + GSD planning/orchestration + research-phase use; honest-but-careful framing of failures as learning moments.
- **DOC-04:** Live `demo.sh` — <60s wall-clock 6-policy sweep on a ~5K-request trace; sources `.env`; rehearsed 3+ times on the target laptop with a full screen-recording backup.

In scope: writing, figure curation, demo scripting, rehearsal. All source code / simulator changes complete (Phases 1-5). New capabilities belong in other phases.

</domain>

<decisions>
## Implementation Decisions

### Paper Narrative (DOC-02)

- **D-01:** **Lead with the surprise finding.** Open the paper with the unexpected result — W-TinyLFU dominates Court by 4-5pp but essentially ties SIEVE on Congress across the full α range. The rest of the paper unpacks *why*, walking workload characterization → policy results → mechanism deep-dive. Hook-driven structure matches the midpoint-feedback ask: "explain why one algorithm wins."
- **D-02:** **Hybrid formal + first-person tone.** Third-person for results and methodology sections ("W-TinyLFU wins 11/12 MRC cells"). First-person for methodology choice justifications and the entire DOC-03 AI-use report ("We chose replay-Zipf because..."). Standard CS-class-report register.
- **D-03:** **One deep finding + breadth elsewhere.** The SIEVE≈W-TinyLFU-on-Congress mechanism (from `05-ANALYSIS.md`) is the 1-2-page centerpiece. All other findings — SHARDS validation, ablations, byte-MRC outlier story — get paragraph-length treatment. This is the direct response to the professor's "more analysis of *why*" ask.
- **D-04:** **Figure-led opening on page 1.** Page 1 opens with `compare_mrc_2panel.pdf` (the canonical 2-panel Congress│Court MRC with ±1σ bands) plus a caption/callout pointing at the high-α divergence. Body text frames the question the paper answers.
- **D-05:** **Practitioner decision tree lives in the conclusion.** The `winner_per_regime_bar.pdf` + decision-tree table are the paper's take-home payoff at the end — reader leaves with "if your workload looks like Congress, SIEVE or W-TinyLFU (tied); if it looks like Court, W-TinyLFU by ~5pp."

### AI-Use Report (DOC-03)

- **D-06:** **Decision-log format.** Chronological log of concrete decisions — "Claude suggested X, I chose Y because Z", "Claude missed this bug, I caught it in review", "I trusted Claude here, in retrospect I shouldn't have." Shows judgment + collaboration pattern. Matches professor's "what worked and didn't" framing.
- **D-07:** **Actual PROCESS.md bug count, not pinned to literal "9".** The original midpoint requirement said "9-bug list" but the real count in `PROCESS.md` may differ by now. Audit the bug tables across Round 1, Round 2, SHARDS, and Plan 04-* deviations; use whatever count is accurate and note the revised number explicitly.
- **D-08:** **Balanced-but-careful honesty on failures.** Include concrete "what didn't work" moments (over-trusting confident-sounding wrong answers, bugs Claude shipped, manual verifications required) BUT frame them as learning moments rather than "Claude was wrong." Diplomatic register appropriate for a graded class report; avoids sanitized successes-only tone.
- **D-09:** **Scope: Claude Code + GSD planning/orchestration layers.** The report discusses both the implementation partnership with Claude Code AND the meta-layer — how GSD multi-phase planning, subagent orchestration, and parallel worktree execution structured the project. Signals sophisticated AI collaboration beyond "I asked Claude to write code."
- **D-10:** **Research-phase discussion gets equal weight with implementation.** How AI shaped *what* was built (picking CourtListener over PACER, selecting W-TinyLFU as the 6th policy, choosing replay-Zipf) is treated with the same depth as the implementation partnership. Whole-project view, not just code-collaboration.

### Figure Curation (DOC-02 figures)

- **D-11:** **6-8 figures main body + 10-12 appendix.** Main body carries the narrative; appendix is for reproducibility-minded readers. Forces editorial discipline without losing data.
- **D-12:** **Must-have main-body figures:**
  - `results/compare/figures/compare_mrc_2panel.pdf` — opening hook + centerpiece (D-04)
  - `results/compare/figures/winner_per_regime_bar.pdf` — conclusion takeaway figure (D-05)
  - `results/shards_large/figures/shards_mrc_overlay.pdf` — SHARDS rigor / methodology signal
- **D-13:** **2nd-tier main-body figures:**
  - `results/{congress,court}/figures/alpha_sensitivity.pdf` — only place the "W-TinyLFU vs LRU at every α" story lands; foundation for the mechanism argument
  - `results/{congress,court}/figures/ablation_doorkeeper.pdf` — Doorkeeper marginal-hedge finding (Phase 4 ablation)
  - `results/{congress,court}/figures/workload.pdf` — grounds workload characterization claims in a picture of actual traffic
- **D-14:** **Dedicated "Ablations" section in main body.** All three ablation figures (`ablation_s3fifo`, `ablation_sieve`, `ablation_doorkeeper`) live together as a rigor signal, with each one briefly motivated. Everything else (the per-workload MRC, byte-MRC, OHW figures from Phase 1) moves to appendix.

Final main-body count target: ~10 figures (slightly above the 8 target) — acceptable given the ablations section structure.

### Demo (DOC-04)

- **D-15:** **Live 6-policy sweep on small trace.** `demo.sh` runs `./cache_sim` on a pre-loaded ~5K-request Congress trace with all 6 policies at 3-4 cache sizes, prints the miss-ratio table live, then renders one figure (likely `compare_mrc_2panel.pdf` or a live-generated subset). Tangible: audience sees real code running in real time. Matches DOC-04 spec literally.
- **D-16:** **Single full-demo screen recording as backup.** One end-to-end recording of `demo.sh` completing on the target laptop, saved locally. If live demo fails, cut to the recording — audience gets the same content. Simple, low-effort, literal match to the "screen-recording backup" requirement.
- **D-17:** **demo.sh self-sources `.env`.** The script sources `.env` at the top, which sets `DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib`, `PYTHONPATH=.venv/lib/python3.14/site-packages`, `COURTLISTENER_API_KEY` (if needed), and any other variables the Phase 1-3 workflow established. Single-command invocation on the target laptop.
- **D-18:** **3 rehearsals logged to `demo-rehearsal.log`.** Run `demo.sh` end-to-end at least 3 times on the actual demo laptop; capture each run's wall-clock + stdout to a log file; commit the log as evidence. Tests environment setup + timing + output formatting against the literal DOC-04 "tested 3+ times" requirement.
- **D-19:** **~5K-request pre-loaded demo trace.** Pre-generate a 5K-request slice (first 5000 lines of `traces/congress_trace.csv`, or a seeded sample — Claude's discretion). Gives ~30s wall-clock for the 6-policy sweep at 3-4 cache sizes; leaves ~30s buffer for figure render + narration. Size balances "compelling numbers" vs "fits in 60s budget on a slow laptop."

### Claude's Discretion

- Specific paper section titles and ordering (beyond the opening hook and closing decision tree).
- Which rehearsal-failure-mode the screen recording prioritizes (just the main happy-path recording is sufficient).
- Exact count of bugs in the PROCESS.md audit — use whatever the actual count resolves to.
- Whether to use markdown + pandoc vs LaTeX vs Typst for the paper pipeline — pick what's lowest-friction on the target laptop.
- Whether the demo trace is the first 5K lines verbatim or a seeded sub-sample (Claude's discretion per D-19).
- Appendix ordering and section structure.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Analysis source material (the paper is built from these)

- `.planning/phases/05-cross-workload-analysis-infrastructure/05-ANALYSIS.md` — **Primary input for DOC-02.** Contains the mechanism explanation, workload-trait → policy-behavior story, SIEVE-vs-W-TinyLFU analysis, byte-MRC outlier warning, paper-section-to-analysis-section mapping, and the Phase 5 addendum with refreshed Court data + Congress α ∈ {1.3, 1.4, 1.5} crossover test.
- `results/compare/workload_characterization.md` — ANAL-03 table (α, OHW, size dist, working set for both workloads).
- `results/compare/winner_per_regime.md` — ANAL-04 table (4 regimes × 2 workloads winners).
- `results/compare/aggregated/**/*_aggregated.csv` — 5-seed mean + std + p_value + significance data.
- `results/compare/figures/*.pdf` — 4 cross-workload figures (compare_mrc_2panel, compare_policy_delta, compare_mrc_overlay, winner_per_regime_bar).

### AI-use report source material (DOC-03)

- `PROCESS.md` — **Bug list source of truth for DOC-03.** Contains bug tables across Round 1, Round 2, SHARDS denominator, and phase-specific deviations. Audit the actual count (D-07).
- `.planning/PROJECT.md` — Key Decisions table; maps "what was chosen and why" for the research-phase discussion (D-10).
- All `.planning/phases/*/XX-CONTEXT.md` files — Decision histories showing the back-and-forth between user vision and Claude implementation.
- All `.planning/phases/*/XX-SUMMARY.md` files — Post-execution summaries showing what Claude built, what deviated from plan, and why.
- `.planning/phases/05-cross-workload-analysis-infrastructure/05-REVIEW.md` — Code review findings (2 warnings, 10 info) — useful for "what Claude missed that review caught" framing.
- `.planning/STATE.md` — "Accumulated Context / Decisions" section — phase-by-phase decision log.

### Phase 1-5 artifacts (methodology + rigor citations)

- `include/hash_util.h` — FNV-1a extraction (Phase 1, REFACTOR-01).
- `include/wtinylfu.h` + `include/count_min_sketch.h` — W-TinyLFU implementation (Phase 2; Caffeine v3.1.8 with 6 deliberate CMS deviations documented in `02-01-CAFFEINE-NOTES.md`).
- `include/doorkeeper.h` — Doorkeeper Bloom filter (Phase 4 Plan 04-02).
- `scripts/collect_court_trace.py` — CourtListener REST v4 collector (Phase 3 Plan 03-01; 609 lines with SSRF mitigation + defensive checks).
- `scripts/run_multiseed_sweep.py` — Multi-seed orchestrator (Phase 5 Plan 05-03).
- `scripts/compare_workloads.py` — Aggregation + Welch's t-test pipeline (Phase 5 Plans 05-04 + 05-06).
- `scripts/plot_results.py` — 4 cross-workload plot functions added (Phase 5 Plan 05-05).
- `scripts/check_anal_acceptance.py` + `scripts/check_wtlfu_acceptance.py` — Phase 2 + Phase 5 acceptance gates.
- `Makefile` — Build + sweep + plots targets. Note: `make plots` requires `DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib` + `PYTHONPATH=.venv/lib/python3.14/site-packages` on macOS (Phase 1 commit 2dc8466). `demo.sh` MUST source this (D-17).

### Repository-root artifacts

- `README.md` — 124 lines; existing project README. May need update for Phase 6 (deferred decision).
- `PROCESS.md` — 232 lines; canonical bug-list source for DOC-03.

### Requirements traceability

- `.planning/REQUIREMENTS.md` §DOC-02, §DOC-03, §DOC-04 — the three requirements this phase closes.
- `.planning/ROADMAP.md` Phase 6 section — phase goal + SC-1..4 locked statements.

### External/academic references likely cited in DOC-02

- Yang et al., SOSP '23 — S3-FIFO paper (already cited in PROCESS.md for the 10% default → ablation).
- Zhang et al., NSDI '24 — SIEVE paper (visited-bit mechanism claim validated by our ablation).
- Einziger + Friedman, TinyLFU paper — Doorkeeper + CMS admission filter design.
- Waldspurger — SHARDS paper (MAE target context in Phase 4 Plan 01).
- Clauset et al. — Zipf MLE estimator (used in Phase 1 workload characterization).
- Caffeine project — source of truth for W-TinyLFU port (v3.1.8 specifically).

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

- **The entire simulator is complete.** Phase 6 does NOT modify `src/`, `include/`, or any C++ code. demo.sh invokes the existing `./cache_sim` binary built via `make`.
- **All figures already exist.** `scripts/plot_results.py` generates the full set. Phase 6 selects a subset; it doesn't write new plot functions.
- **All analysis artifacts already exist.** `results/compare/*.{md,json}`, `workload_characterization.md`, `winner_per_regime.md` are already assembled. Phase 6 copies/adapts them into the paper; it doesn't re-run analysis.
- **`05-ANALYSIS.md` pre-writes the paper's analysis chapter.** Prose can be lightly adapted from there — it's already structured with "paper outline implications" mapping.
- **PROCESS.md is the DOC-03 backbone.** 232 lines of pre-written bug + decision history. DOC-03 is largely a reframing + expansion task, not original writing.

### Established Patterns

- **Make-based workflow.** All Phase 1-5 pipelines run through `Makefile` targets (`make`, `make test`, `make run-sweep`, `make plots`). demo.sh should use the existing `make` invocations where possible rather than reinventing command-line flags.
- **DYLD_LIBRARY_PATH / PYTHONPATH workaround is load-bearing.** On the target laptop (macOS 25.2.0, Python 3.14.4 via Homebrew), the expat library symbol mismatch requires the Phase 1 Makefile-level env vars. demo.sh MUST preserve this.
- **Gitignored artifacts.** `results/` is gitignored per D-15 (Phase 5). The paper references figures/data in `results/` but those must be regenerable via `make run-sweep` + `python3 scripts/compare_workloads.py` + `make plots`. Paper commit does NOT embed the rendered PDF figures in git history.
- **5-seed Welch's t-test** for any significance claim in DOC-02 body text. The aggregated CSVs have `p_value` and `significant` columns per cell.

### Integration Points

- `PROCESS.md` is currently at repo root; DOC-03 either lives alongside it or replaces it. Decide during planning.
- Paper PDF output path: Claude's discretion. Suggest `docs/DOC-02-final-report.pdf` or `docs/final-report.pdf`. Source (markdown or LaTeX) alongside.
- `demo.sh` lives at repo root (alongside `Makefile`).
- Screen recording saved to `docs/demo-backup.mov` (or similar). Gitignored if large — or committed at moderate quality.
- `demo-rehearsal.log` committed to repo as evidence of D-18 compliance.

</code_context>

<specifics>
## Specific Ideas

- **Opening figure callout wording (from D-04):** Something like: "*Note the 4-5pp gap between W-TinyLFU and SIEVE on Court vs the photo-finish on Congress at high α. This paper explains why — it's not about the policies. It's about the workloads.*"
- **Analysis section centerpiece (from D-03):** The SIEVE≈W-TinyLFU-on-Congress finding is the deep-dive. Source material: `05-ANALYSIS.md` §"Why SIEVE Ties W-TinyLFU on Congress (and Loses on Court)". Include the per-α gap table (Congress vs Court) as a mini-figure.
- **Decision tree closing (from D-05):** Frame as: *"Use this flowchart when picking an eviction policy for a public-records API workload. If your raw trace has α_raw < 0.5 and near-uniform size distribution → SIEVE or W-TinyLFU tied. If α_raw > 0.8 and long-tail size distribution → W-TinyLFU by ~5pp. If you have a catastrophic size outlier → W-TinyLFU's admission filter matters most at small cache sizes."*
- **AI-use report opening hook (from D-06):** Start with a concrete decision moment. Candidate: "*Claude initially wrote S3-FIFO's ghost eviction using `unordered_set::begin()` — random pick, not FIFO. Round 1 review caught it. This bug is the whole AI-collaboration story in miniature: fluent, plausible, structurally wrong. The rest of this report is about learning which instincts to develop.*"
- **Demo narration arc (from D-15):** *"Here's 5,000 requests of real Congress.gov traffic. We'll run all 6 policies on it at 3 cache sizes. Watch the miss ratio drop as cache grows — and watch W-TinyLFU beat LRU by 10 percentage points at small cache. This whole run is under 30 seconds."*

</specifics>

<deferred>
## Deferred Ideas

Nothing that came up during discussion was scope creep — all questions stayed within DOC-02 / DOC-03 / DOC-04. Items deferred to v2 (carried forward from PROJECT.md, unchanged):

- **V2-01:** Caffeine trace cross-validation (±2% agreement) — needs access to published Caffeine benchmark traces.
- **V2-02:** LHD or AdaptSize as 7th policy — size-aware eviction.
- **V2-03:** Third trace domain (SEC EDGAR) — complete the public-records trilogy.

New deferred-to-v2 items surfaced during Phase 5 analysis:

- **V2-04:** Multi-seed α ∈ {1.3, 1.4, 1.5} extension on Congress — would turn the single-seed crossover test into CI-backed data. Cheap to add (~30s wall-clock).
- **V2-05:** Multi-seed byte-MRC aggregation — the per-seed CSVs already have byte_miss_ratio; aggregator just doesn't use it. One-line change to compare_workloads.py.
- **V2-06:** Code review WR-01 / WR-02 latent-coupling fixes in plot_results.py (BASE_POLICIES filter missing on winner-bar; regime dispatch missing else clause). Only manifest if future work adds ablation variants to the multi-seed sweep.

These v2 items should be mentioned in DOC-02's "Limitations / Future Work" section so readers know they're acknowledged, not invisible.

</deferred>

---

*Phase: 06-writeup-demo*
*Context gathered: 2026-04-21*
