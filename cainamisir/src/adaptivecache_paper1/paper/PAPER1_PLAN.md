# Paper 1 — Plan

## STATUS (2026-04-28): Gate B failed → reframe to honest negative result

**Phases B/C/D/E shipped 5+ heuristic compaction policies × 2 benchmarks × 2 model classes (Qwen3-30B-A3B, Haiku 4.5).** Aggregate finding:

- **No heuristic compaction policy Pareto-dominates `none`** on a strong agent (Haiku) at any benchmark we ran (τ-bench airline, τ-bench retail, SWE-bench Lite N=4, N=10).
- **On τ-bench (airline + retail single-customer): nothing to compact** — max prompt averages 9-15K tokens, well under any sane trigger. 0 compactions across all policies.
- **On SWE-bench Lite Haiku N=10:** plain `consumption_evict` ties `none` at 5/10 real-resolved; cost ~2.2× higher. Outline mode ties resolve, costs ~2.4× more. Facts mode regresses to 4/10. *No policy on the Pareto frontier matches `none`.*
- **Below an agent-quality threshold (Qwen3-30B-A3B, single seed):** `smart_evict` beats `none` because it prevents catastrophic failure modes (false-submit, context overflow) — but the win is single-seed RNG-fragile and doesn't replicate at multi-seed.

**This was anticipated.** Gate B (this document, original) says: *"at least ONE policy beats `none` by ≥10% on lifetime cost at equal resolve rate on at least ONE benchmark × model cell. If no — hard reframe needed (maybe to the negative-result framing)."* Reframing now.

### Working title (revised)

**The Cliff Tax: Why Training-Free Heuristic Compaction Loses to No-Compaction on Strong Agents**

### Pitch (revised)

Lifetime cost of long-running LLM agents is dominated by prefix-cache reuse. Every compaction event invalidates downstream cache, billing the next K tokens at 10× cost. We show empirically that across 8 training-free heuristic compaction policies (FIFO, naive_summary, microcompact, prefix_preserving, position_aware, boundary_aware, smart_evict, llm_reorganizer, and our novel **action-graph supersession** family) and 2 benchmark classes (coding-agent, customer-service tool-use), no policy Pareto-dominates `none` on a strong agent. The cliff tax exceeds the bytes-saved benefit on every realistic configuration. We characterize *why* — including a mechanism finding that **placeholder design dominates resolve outcomes** (less informative is more, because structural hints anchor the agent's hypotheses) — and use this as the empirical case for Paper 2's KV-pointer recall (offload bytes, keep KV, no cliff).

### Positive contributions inside the negative result

1. **Lifetime-cost framing + measurement.** The `pipeline/pricing.py` infrastructure attributes cost to policy events properly (cliff cost, summarizer call cost, cache write cost) across 5 provider price columns.
2. **Action-graph supersession as a novel signal.** The `consumption_evict` family uses agent tool-call semantics (read_file→edit_file consumes; run_tests cascade; etc.) to identify *confirmed-stale* obs without LLM scoring or attention. No prior compaction system uses tool-graph supersession; we ship it open-source. **It works mechanistically** (correctly tags consumed obs, max prompt drops 30%) — but the cliff overhead still loses on cost.
3. **Placeholder-design ablation (Phase E v1).** Three placeholder variants on the same eviction signal: `[evicted: ~N tokens]` (plain), `[… — fact: <function defs>]` (facts), `[… outline: L1 imports, L17 class …]` (outline). On `pytest-7490` we see plain wins, facts loses (anchoring loop), outline wins. The mechanism story: a structural-hint placeholder *anchors* the agent on surface structure, inducing edit-revert loops on misidentified functions. **Less is more.** This is a publishable nugget.
4. **Real-test validation infrastructure.** `scripts/validate_with_tests.py` replays agent edit_file calls onto fresh checkouts at base_commit, applies `test_patch`, runs FAIL_TO_PASS in per-instance Python 3.9 venvs. Replaces line-overlap oracle (which overcounted by ~30% on our N=10 set). Reusable for any agent harness.
5. **Empirical motivation for Paper 2.** Our negative result is the cleanest empirical case for KV-pointer recall: byte-level eviction is fundamentally bounded by the cache-cliff cost-amplification factor (~10×). Keeping the bytes (offload-and-recall) is the only way around it.

### Open paper-quality items before submission

- [ ] **Multi-seed at N=10 SWE-bench Lite** (3 seeds × 4 policies × 10 tasks = 120 trajectories, ~$15 Haiku, ~3 hr wall). Establishes whether 5/10 vs 4/10 differences are above sampling noise. Required for any robust claim.
- [ ] **Paired t-test** on per-task cost (`none` vs each policy) across seeds. Drives the "no Pareto domination" claim from descriptive to inferential.
- [ ] **Multi-customer τ-bench chain harness** (optional but compelling): extend `taubench.py` with `chain_size: int` so N customer tasks are stitched into one ~150-turn trajectory. Tests whether compaction wins when context *forces* it. ~50 LOC, ~$8 to run N=10 chains × 4 policies.
- [ ] **One figure per contribution claim.** Need: (a) Pareto frontier across all phases, (b) cliff-distribution histogram per policy, (c) cost-decomposition stacked bars, (d) placeholder-design ablation bar (the pytest-7490 mechanism finding), (e) provider-invariance table.

---

## Working title (legacy, retained for diff)

**AdaptiveCache: Importance-Scored Compaction for Agentic Context Management**

## One-line pitch

For long-running LLM agents, lifetime cost is dominated by repeatedly sending huge tool observations across many LLM calls; we propose an inference-time policy that uses an LLM-as-reorganizer to score message importance and compact aggressively without losing task quality, and we show across multiple benchmarks and providers that it Pareto-dominates fixed heuristics (FIFO, naive_summary, microcompact, prefix_preserving).

## Contribution claims (what's actually new)

1. **Lifetime-cost framing.** Existing compaction work measures "cost at peak context" or "tokens saved per compaction." We measure $ per resolved task across the *full* trajectory including the cache-cliff cost a compaction event imposes on the next LLM call. This metric properly attributes cost to the policy, not just the moment.
2. **Empirical measurement of context structure on real agent traces.** Three independent importance proxies (citation, embedding similarity, attention) are essentially uncorrelated on Hermes Agent Reasoning Traces. Position primacy holds across model sizes (Qwen3 0.6B–8B, 500–1500× attention concentration in first 10%). Tool obs are 77% of agent-loop tokens; top 10% biggest tool obs hold 70% of mass; big tool obs *get* attention (so naive size-based eviction is wrong).
3. **LLM-as-reorganizer policy.** A small auxiliary LLM (Qwen3-0.6B-Instruct or similar) scores per-message importance + proposes a compaction layout. We show it dominates rule-based policies at equal compute budget.
4. **Provider-invariant evaluation framework.** We re-cost each trajectory under five different price columns (Anthropic Haiku/Sonnet, OpenAI gpt-4.1/mini, Qwen self-host) and report whether the policy ranking holds. Distinguishes generalizable from provider-specific findings.

## Non-goals (explicit)

- We do **not** train the serving model. Everything is inference-time, drop-in.
- We do **not** require a custom vLLM fork. Compatible with any OpenAI-style endpoint.
- We do **not** target the multi-tier KV hierarchy (NVIDIA Dynamo's territory). Our policy can run *on top of* any backend that supports prefix caching.

## Datasets / benchmarks

| Benchmark | Why | Resolve metric | Mean trace length |
|---|---|---|---|
| **longdoc** (synthetic, ours) | controlled, reproducible, deterministic resolve metric (sum-of-needle-values), forces compaction | exact match | configurable, default 80K doc + 4 needles |
| **τ-bench airline** | real multi-turn customer-service dialogues, 115 tasks, public, real ground truth | `env.calculate_reward()` | 20-50 turns |
| **SWE-bench Verified** (subset) | gold-standard agentic coding bench; expensive but necessary for a paper | `sb-cli` evaluation | 30-100 turns |
| **Hermes Agent Reasoning Traces** | only used for *measurement* sections (context structure analysis), not policy eval | — | — |
| **Applied Compute workloads** | only used for cost-shape stress test (replay-only, no agent loop) | — | — |

## Models tested

| Role | Models | Notes |
|---|---|---|
| Serving (the agent) | Qwen3-30B-A3B (Modal), gpt-4.1-mini (OpenAI), claude-haiku-4-5 (Anthropic) | three provider classes for the invariance claim |
| Reorganizer (small) | Qwen3-0.6B / Qwen3-1.7B | local, fast, cheap |
| Embedding (for proxy comparison) | sentence-transformers/all-MiniLM-L6-v2 | for the measurement section only |

## Policies compared

Already in `pipeline/policies/`:
- `none` — no compaction, baseline
- `naive_summary` — replace middle with one summary
- `microcompact` — per-large-msg in-place summarization (Claude-Code-style)
- `prefix_preserving` — frozen [first K turns] + summary + recent
- `boundary_aware` — defer compaction to detected boundaries

New:
- `llm_reorganizer` — auxiliary LLM scores per-message importance + emits compaction plan; we apply it
- `position_aware_heuristic` — pin first 10% + drop low-attention-bucket middle (no LLM) — this is the "structural-only" baseline you wanted

## Scope of evaluation

For each `(serving_model, benchmark, policy)` cell:
- N=20 instances minimum, N=50 if budget allows
- 3 seeds per cell (for variance)
- Budget settings: tight (50% of natural overflow), nominal (= overflow point), loose (no overflow expected)
- Capture: per-step usage, final answer, resolved/not, cliff events, compaction count, summarizer-call cost

Total cells: 3 models × 3 benchmarks × 7 policies × 3 budgets × 3 seeds = **567 trajectories**. With longdoc cheapest and SWE-bench Verified most expensive, total API budget ~$200-500 depending on how aggressive we go on SWE-bench.

## Headline figures (planned)

1. **Pareto** — cost vs resolve rate across all (model, benchmark, policy). The headline. Hopefully `llm_reorganizer` is on the frontier.
2. **Cliff distribution** — the empirical distribution of cliff sizes per policy on real agent traces. Already largely done from Dataset 1.
3. **Cross-provider invariance** — same policy, different price column, ranking preserved. Already done for AC; redo for the new runs.
4. **Position-importance heatmap** — already done for Hermes attention; mention as motivation.
5. **Lifetime cost decomposition** — bars showing uncached / cached / output / compaction-call cost per policy.

## Phased plan (concrete, 6-8 weeks)

### Phase A — Infrastructure shakedown (Week 1)
- [ ] Confirm `modal_app/serve_unified.py` Qwen3-30B-A3B endpoint is alive (or redeploy if it isn't)
- [ ] Wire `pipeline/runner.py` end-to-end with the longdoc benchmark — single trace must complete
- [ ] Wire τ-bench: install `tau-bench` package, verify `env.calculate_reward()` returns sane values
- [ ] Get a baseline `none` resolve rate on each benchmark (5 instances each)
- [ ] **Gate:** if `none` doesn't even run end-to-end on longdoc, fix that before anything else

### Phase B — Implement the new policies (Week 2)
- [ ] `pipeline/policies/llm_reorganizer.py` — small LLM scores messages, returns compaction plan
- [ ] `pipeline/policies/position_aware_heuristic.py` — structural-only baseline (no LLM, no regex keywords)
- [ ] Unit tests for both (extend `tests/test_policies.py`)
- [ ] Smoke test on 1 longdoc trace per new policy
- [ ] **Gate:** new policies must produce cliff numbers within 2× of theory; otherwise debug

### Phase C — Run the matrix (Weeks 3-4)
- [ ] longdoc × all 3 models × all 7 policies × N=20 × 3 seeds (~1260 trajectories, mostly free if Qwen self-hosted)
- [ ] τ-bench × Qwen + gpt-4.1-mini × all 7 policies × N=20 × 3 seeds (~840 trajectories, ~$50)
- [ ] SWE-bench Verified × Qwen + claude-haiku × top-3 policies × N=20 × 1 seed (~120 trajectories, ~$200)
- [ ] All trajectories saved as JSONL, each row = full Step list per existing `Trajectory` schema

### Phase D — Analysis (Week 5)
- [ ] Pareto plot — cost vs resolve rate, all cells
- [ ] Cross-provider invariance check — for each benchmark, does ranking hold across provider price columns?
- [ ] Cliff distribution per policy on the new data
- [ ] Per-policy cost decomposition stacked bars
- [ ] Ablations: keep_first_turns sweep, trigger_ratio sweep, summarizer_compression sweep
- [ ] Identify the surprising finding (every paper needs one) — likely candidate: `llm_reorganizer`'s overhead is amortized away by better eviction quality, OR `position_aware_heuristic` is competitive without any LLM, OR the cliff is amortized for sane policies

### Phase E — Writing (Weeks 6-8)
- [ ] Draft introduction + related work (lots already in `wiki/`, just adapt)
- [ ] Methods section — lifetime cost metric, simulator, runner, policy descriptions
- [ ] Results — measurement, eval, ablations
- [ ] Discussion — what generalizes, threats to validity, future work (Paper 2 teaser)
- [ ] Anonymize, prepare submission

## Risks (named, with mitigations)

| Risk | Likelihood | Mitigation |
|---|---|---|
| `llm_reorganizer`'s overhead exceeds its compaction savings → ties or loses to rule-based policies | medium | report as honest finding; may motivate distillation or trained scoring (lead-in to Paper 2) |
| τ-bench package API changes → adapter breaks | low | pin a version, vendor it locally if needed |
| SWE-bench Verified resolve rates collapse for our small-ish models | high | measure relative deltas only; not chasing SOTA |
| Provider price changes between paper draft and submission | low | report all numbers in a `pricing.yaml` snapshot; redo cost math is one script |
| Modal endpoint becomes unavailable or expensive | medium | longdoc + τ-bench are deterministic; can rerun on any vLLM endpoint |

## Decision gates

Same as the lifetime_cost study originally:
- **Gate A** (after Phase A): `none` resolve rate on longdoc ≥ 80% — confirms the agent loop actually works. If <50% something's wrong with our agent harness, fix before continuing.
- **Gate B** (after Phase C): at least ONE policy beats `none` by ≥10% on lifetime cost at equal resolve rate on at least ONE benchmark × model cell. If no — hard reframe needed (maybe to the negative-result framing).
- **Gate C** (after Phase D): policy ranking holds across at least 2 provider price columns on at least 1 benchmark. If no — pitch becomes provider-specific.

## What we already have (counts toward this paper)

- Lifetime cost simulator + 5 policies (`pipeline/policies/`)
- AC + Hermes measurement code (`extract_attention.py`, `extract_relevance.py`, `analyze_*.py`, `measure_tool_obs_share.py`)
- Cross-provider price sheet (`pricing.yaml`)
- Hermes 3-way correlation finding (citation/embedding/attention all uncorrelated)
- Qwen3 0.6B-8B attention extraction confirming position primacy invariance
- Recorded Haiku trajectories with real cache_read_tokens for cliff measurement
- 17 unit tests passing
- Synthetic `longdoc` benchmark code (needs end-to-end shakedown)
- τ-bench / GAIA / SWE-bench replay adapters (need shakedown)

Roughly 60% of the codebase a paper would need is already in place. Phase A-B is mostly verification; the new code is `llm_reorganizer.py` and `position_aware_heuristic.py`.

## Tracked-but-deferred (won't make this paper)

- KV-offload-with-recall (→ Paper 2)
- Trained importance scorer (→ Paper 2)
- vLLM custom backends (→ Paper 2)
- Multi-agent / subagent settings (cite NVIDIA Dynamo's blog, defer)
