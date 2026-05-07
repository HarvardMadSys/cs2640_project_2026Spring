# Phase 6: Writeup & Demo - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-21
**Phase:** 06-writeup-demo
**Areas discussed:** Paper narrative arc, AI-use report framing, Figure set pruning, Demo content + failure hedge

---

## Area Selection

| Option | Description | Selected |
|--------|-------------|----------|
| Paper narrative arc | Lead order + structure + tone + depth | ✓ |
| AI-use report framing | Structure + honesty + scope of DOC-03 | ✓ |
| Figure set pruning | Main body vs appendix + must-haves | ✓ |
| Demo content + failure hedge | What demo shows + backup strategy | ✓ |

**User's choice:** All four selected (multi-select).

---

## Paper Narrative Arc

### Q1: What should the paper LEAD with in its opening?

| Option | Description | Selected |
|--------|-------------|----------|
| Surprise finding | Open with "W-TinyLFU dominates Court by 4-5pp but ties SIEVE on Congress — why?" Hook-driven. | ✓ |
| Conventional structure | Workload char → policies → results → analysis. Safe academic. | |
| Problem-first framing | Open with practitioner question, build toward decision tree. | |
| Methodology-first | Open with rigor claims (5-seed CI, Welch's, SHARDS). | |

**User's choice:** Surprise finding (recommended).

### Q2: What writing tone fits the course context?

| Option | Description | Selected |
|--------|-------------|----------|
| Hybrid formal + first-person | 3rd person for results/methodology; 1st person for choices + AI-use report. | ✓ |
| Formal academic | Passive voice, 3rd-person throughout. | |
| Accessible / first-person | 1st-person active voice throughout. | |

**User's choice:** Hybrid formal + first-person (recommended).

### Q3: How should we balance breadth vs depth in the analysis section?

| Option | Description | Selected |
|--------|-------------|----------|
| One deep finding + breadth | SIEVE≈W-TinyLFU-on-Congress mechanism as 1-2 page centerpiece; everything else paragraph-length. | ✓ |
| Even breadth across findings | Equal paragraph treatment of all findings. | |
| Two deep findings | Both the mechanism AND the Court byte-MRC outlier story as deep-dives. | |

**User's choice:** One deep finding + breadth (recommended).

### Q4: How should the surprise-finding opening be literally delivered on page 1?

| Option | Description | Selected |
|--------|-------------|----------|
| Figure-led hook | compare_mrc_2panel on page 1 + callout pointing to the high-α divergence. | ✓ |
| Number-led hook | Open with key numbers in prose; no figure on page 1. | |
| Question-led hook | Open with motivating question + preview the mechanism. | |

**User's choice:** Figure-led hook (recommended).

### Q5: Where does the practitioner decision tree / winner-per-regime land?

| Option | Description | Selected |
|--------|-------------|----------|
| Conclusion / takeaway | Decision tree as the paper's take-home payoff at the end. | ✓ |
| Front matter / abstract-plus | Decision tree as graphical-abstract-style summary at front. | |
| Body / results section | Decision tree as one subsection in results — no special emphasis. | |

**User's choice:** Conclusion / takeaway section (recommended).

**Notes:** Paper is hook-driven, mechanism-centered, with the practitioner decision tree as the closing payoff. Hybrid tone allows concrete first-person discussion of methodology choices without sacrificing systems-paper register for results.

---

## AI-Use Report Framing

### Q1: How should the AI-use report be structured?

| Option | Description | Selected |
|--------|-------------|----------|
| Decision log | Chronological log of concrete decisions: "Claude suggested X, I chose Y because Z". | ✓ |
| Taxonomy (can/can't) | Categorical: "What AI is good at" vs "what AI struggles with". | |
| Chronological relationship arc | Story of the partnership, early over-trust → learned judgment. | |
| Bug-driven | Each bug is a subsection covering AI role + catch + lesson. | |

**User's choice:** Decision log (recommended).

### Q2: How many bugs should the canonical "bug list" include?

| Option | Description | Selected |
|--------|-------------|----------|
| Count what's actually in PROCESS.md | Audit the real count; don't pin to literal "9" if actual count differs. | ✓ |
| Keep at "9 bugs" for continuity | Present 9 canonical ones matching original requirement language. | |
| Pick ~5 deepest bugs | Focus on 5 highest-signal bugs for depth per bug. | |

**User's choice:** Count what's actually in PROCESS.md (recommended).

### Q3: How honest should the "what didn't work" section be?

| Option | Description | Selected |
|--------|-------------|----------|
| Unflinching honesty | Include over-trust failures, confident-but-wrong Claude moments, manual verification stories. | |
| Balanced but careful | Include failures framed as "learning moments" rather than "Claude was wrong." Diplomatic. | ✓ |
| Successes-focused | Mostly cover what worked, brief pitfalls section. | |

**User's choice:** Balanced but careful. **Notes:** User explicitly chose the middle-ground register over unflinching honesty — diplomatic framing appropriate for a graded class report.

### Q4: What scope does the AI-use report cover?

| Option | Description | Selected |
|--------|-------------|----------|
| Claude Code only | Implementation partnership only; no planning/orchestration meta-layers. | |
| Claude Code + planning/orchestration layers | Also discusses GSD workflow, subagent orchestration, parallel worktree execution. | ✓ |
| All AI interactions broadly | Every AI touchpoint: Claude Code, research, auto-generated content. | |

**User's choice:** Claude Code + planning/orchestration layers. **Notes:** User chose the BROADER option over the safer Claude-Code-only scope — showing the sophisticated multi-layer AI workflow.

### Q5: How do we handle the research-phase discussion (pre-Phase 1)?

| Option | Description | Selected |
|--------|-------------|----------|
| Brief mention, focus on implementation | 1-2 paragraphs on research AI-use; bulk on implementation bugs/decisions. | |
| Equal weight with implementation | Research and implementation get equal treatment — "how AI shaped WHAT was built". | ✓ |
| Skip entirely | Focus only on code. | |

**User's choice:** Equal weight with implementation. **Notes:** Combined with Q4's broader scope, DOC-03 becomes a whole-project view of AI-native development, not a narrow code-bug log.

---

## Figure Set Pruning

### Q1: How many figures should the main body target?

| Option | Description | Selected |
|--------|-------------|----------|
| 6-8 figures main + 10-12 appendix | Class-report balance between readable and complete. | ✓ |
| 4-5 figures main + full appendix | Very lean main body, full appendix. | |
| 10-12 figures main + no appendix | Denser visuals in main body. | |

**User's choice:** 6-8 figures main + 10-12 appendix (recommended).

### Q2: Which figures are must-have in the main body? (multiSelect)

| Option | Description | Selected |
|--------|-------------|----------|
| compare_mrc_2panel.pdf | Canonical DOC-02 figure: 2-panel MRC with ±1σ bands. | ✓ |
| winner_per_regime_bar.pdf | Takeaway figure at conclusion. | ✓ |
| compare_policy_delta.pdf | Court − Congress miss_ratio per policy. | |
| shards_mrc_overlay.pdf | SHARDS validation figure (exact vs sampling overlay). | ✓ |

**User's choice:** compare_mrc_2panel + winner_per_regime_bar + shards_mrc_overlay. **Notes:** compare_policy_delta excluded — user interpreted it as duplicating info from compare_mrc_2panel.

### Q3: Which of these 2nd-tier figures belong in the main body? (multiSelect)

| Option | Description | Selected |
|--------|-------------|----------|
| alpha_sensitivity figures | Only place "W-TinyLFU vs LRU at every α" story lands. | ✓ |
| ablation_doorkeeper.pdf | Doorkeeper marginal-hedge finding. | ✓ |
| ablation_sieve.pdf | SIEVE visited-bit ablation (+15.4pp contribution). | |
| workload.pdf | Trace timeline / access-pattern visualization. | ✓ |

**User's choice:** alpha_sensitivity + ablation_doorkeeper + workload. **Notes:** ablation_sieve not directly selected — but subsequently the "dedicated Ablations section" answer implies all three ablations are in.

### Q4: Where do the ablation figures live?

| Option | Description | Selected |
|--------|-------------|----------|
| Dedicated "Ablations" section | All three ablation figures in main body, one section. | ✓ |
| Only the most relevant ablation in main body | Doorkeeper in main, others to appendix. | |
| All ablations to appendix | Keeps narrative tight, loses rigor signal. | |

**User's choice:** Dedicated "Ablations" section (recommended). **Notes:** Combined with Q3, this means all three ablation figures (S3-FIFO, SIEVE, Doorkeeper) are in the main body.

---

## Demo Content + Failure Hedge

### Q1: What does the <60s demo actually SHOW?

| Option | Description | Selected |
|--------|-------------|----------|
| Live policy sweep on small trace | ./cache_sim on <10K trace, 6 policies at 3-4 cache sizes, live table + figure. | ✓ |
| Pre-rendered findings tour | Open 4 figures in sequence with narration; no live run. | |
| Interactive α-picker | Audience picks α, demo runs cache_sim with it. | |
| Three-act narrative | Workload stats → live simulator → winner-per-regime table. | |

**User's choice:** Live policy sweep on small trace (recommended).

### Q2: What's the screen-recording backup strategy?

| Option | Description | Selected |
|--------|-------------|----------|
| Single full-demo recording | One end-to-end recording saved locally; cut to it if live fails. | ✓ |
| Segmented recordings per failure mode | Separate recordings for each failure class. | |
| Recording + rehearsed narration script | Recording plus written backup-narration script. | |

**User's choice:** Single full-demo recording (recommended).

### Q3: What's the DYLD_LIBRARY_PATH + .env strategy for demo.sh?

| Option | Description | Selected |
|--------|-------------|----------|
| demo.sh self-sources .env | Script sources .env at top; single-command invocation. | ✓ |
| Hardcoded paths in demo.sh | Hardcode expat/venv paths. | |
| Pre-activate env via README instructions | Two-command invocation. | |

**User's choice:** demo.sh self-sources .env (recommended).

### Q4: How is "tested 3+ times on target laptop" actually structured?

| Option | Description | Selected |
|--------|-------------|----------|
| 3 rehearsals + log | Run 3 times on target laptop, log wall-clock + output to demo-rehearsal.log. | ✓ |
| 3 rehearsals + 1 stress test | 3 normal + 1 adversarial (unplugged, rebooted, etc.). | |
| Single rehearsal on day-of | Fast but violates "3+ times" literal requirement. | |

**User's choice:** 3 rehearsals + log (recommended).

### Q5: What's the small-trace size target for demo.sh?

| Option | Description | Selected |
|--------|-------------|----------|
| ~5K requests, pre-loaded | ~30s wall-clock for 6-policy sweep; ~30s buffer. | ✓ |
| ~10K requests, pre-loaded | 50-55s wall-clock; tight against 60s budget. | |
| ~1K requests, pre-loaded | <10s; noisy miss ratios, less compelling. | |

**User's choice:** ~5K requests, pre-loaded (recommended).

---

## Claude's Discretion

- Specific paper section titles and ordering (beyond opening hook + closing decision tree)
- Markdown/pandoc vs LaTeX vs Typst pipeline for the paper
- Whether the demo trace is first 5K lines verbatim or a seeded sub-sample
- Appendix ordering and section structure
- Exact count of bugs in PROCESS.md audit (actual count, not pinned)

## Deferred Ideas

No scope creep arose during discussion — all four areas stayed within DOC-02/03/04.

**Carried forward as v2 backlog (from PROJECT.md + Phase 5 analysis):**
- V2-01..03: existing (Caffeine cross-validation, LHD/AdaptSize, SEC EDGAR)
- V2-04: Multi-seed α ∈ {1.3, 1.4, 1.5} extension on Congress
- V2-05: Multi-seed byte-MRC aggregation (one-line fix)
- V2-06: Code review WR-01 / WR-02 latent-coupling fixes in plot_results.py

These should be mentioned in DOC-02's "Limitations / Future Work" section per Phase 6 CONTEXT.md's "deferred" guidance.
