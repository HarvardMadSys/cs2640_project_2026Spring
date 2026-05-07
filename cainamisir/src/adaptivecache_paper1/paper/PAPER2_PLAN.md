# Paper 2 — Plan

> **Status (2026-04-28):** Heavily updated after Phase C SWE-bench Lite empirical work
> in `studies/lifetime_cost/`. The Phase C runs (8 successive smokes, both Qwen3-30B-A3B
> and Haiku 4.5 as agents) demonstrated that **heuristic byte-deletion compaction
> cannot beat `none` on a strong agent** at single-seed N=4 — every drop of context
> creates a cache cliff that costs more than the saved bytes save. This is direct
> empirical motivation for Paper 2's KV-pointer approach. Detailed findings:
> `studies/lifetime_cost/reports/FINDINGS.md`.
>
> The plan below is restructured into 6 phases, with a new **Phase 0** that ships
> a training-free, no-vLLM-modification stepping-stone result (action-graph
> supersession + structured consolidation log) before the inference-stack work
> begins. Phase 0 is the bridge from Paper 1 to Paper 2 and is implementable
> today on top of the existing `studies/lifetime_cost/pipeline/` infrastructure.

## Working title

**Mementos as KV Pointers: Speculative Recall for Long-Running LLM Agents**

## One-line pitch

When an agent compacts away earlier context, the *bytes* are gone but the *KV cache* doesn't have to be — we offload it instead, leave a compressed memento behind as a textual pointer, and speculatively prefetch the offloaded KV when the model starts attending heavily to the memento. Best of both worlds: aggressive compaction (text-level summary always visible) + perfect quality recovery when the agent revisits earlier work.

## Empirical motivation from Phase C (2026-04-28)

Eight successive smokes on SWE-bench Lite (config `phase_c_v1` through `phase_c_v8`, summary in `studies/lifetime_cost/reports/FINDINGS.md`) established the following:

1. **Heuristic byte-deletion compaction does not beat `none` on a strong agent.** On Haiku 4.5 + 4 SWE-bench Lite tasks, every compaction policy (`smart_evict`, `prefix_preserving`, `microcompact`, `evict_oldest`, `llm_reorganizer`, `score_periodic`) costs ≥ `none` at equal resolve rate. The cliff cost — input_uncached billed every step after a prefix-byte change — dominates the saved-bytes savings. Single-seed N=4 caveat applies, but the *direction* is consistent across v5, v6, v7, v8.
2. **The mechanism is the prefix cache.** vLLM and the Anthropic API both cache by byte-identity. *Any* mid-prompt byte change invalidates downstream cache, billing the next ~50K-100K tokens of conversation at 10× the cached rate. A 30K-token compaction that fires at step 28 of 38 saves <5K bytes of subsequent prompt — the cliff cost (10× more uncached billing for 8 more steps) exceeds the savings.
3. **`max_drop_score` gating doesn't fix it (it just helps).** v7's `smart_evict` with a strict gate (`max_drop_score=0.4`, only drops `search` exhaust with low ref-count) Pareto-dominated `none` by 22% in one run, but tied or lost in v8. Selectivity is necessary but not sufficient — the cliff cost still dominates whenever the policy fires meaningfully.
4. **The bytes are useful — that's the problem.** In v6 we observed `smart_evict` re-reading `models.py` 5+ times because the original read was holed. The agent's *attention pattern* tells us what's still needed; deletion throws that signal away.

**The cliff is the fundamental obstacle for any byte-level eviction.** This is the empirical case for Paper 2: keep the KV cache for evicted bytes around (as offloaded blocks), so eviction reduces *prompt cost* without invalidating *cache state*. The bytes-on-the-wire become a textual memento; the KV state behind them is preserved and recallable.

## What Phase C built that Paper 2 will reuse

The `studies/lifetime_cost/` infrastructure is largely Paper-2-ready:

- `pipeline/benchmarks/swebench_live.py` — real SWE-bench Lite execution without docker, git-diff resolve oracle
- `pipeline/models/anthropic_native.py` — adapter with proper tool_use/tool_result block conversion + cache_control breakpoints
- `pipeline/models/vllm_local.py` — vLLM 0.10.2 V0 engine wrapper with engine cache singleton (for serving the SFT'd model)
- `pipeline/runner.py` — kick-fix for tool-only environments, vLLM context-overflow handler
- `pipeline/policies/{smart_evict, llm_reorganizer, score_periodic}.py` — comparison baselines
- `scripts/analyze_phase_c.py` — Pareto-frontier analysis with `--exclude_compaction_costs` flag
- `scripts/viewer.py` — Flask-based trajectory inspector at port 5050
- 4 Haiku v6/v7/v8 trajectory sets in `out/phase_c_v*` for direct cost-comparison baselining

Reuse plan: keep all the runner/benchmark/analysis code; the Paper 2 work adds new policies (Phase 0) and a new model adapter for the SFT'd Memento model + KV-recall infrastructure (Phases 2-3).

## What this builds on (and what it adds)

| Component | Existing work | This paper |
|---|---|---|
| Per-block compression | Memento (Microsoft Research, intra-call CoT only) | Inter-call agent loops; tool obs not thinking blocks |
| KV multi-tier offload | NVIDIA Dynamo, LMCache, HiCache, InfiniGen | The **recall trigger** — when to bring back |
| Importance scoring | Paper 1 (this project, heuristic + LLM-reorganizer) | Use these scores to drive recall, not just eviction |
| Prefix caching | vLLM, SGLang | Speculative prefetch driven by attention to mementos |
| Trained compaction | Memento (single LLM CoT), MEM1 (agent state) | Agent-trace dataset (OpenAgentMementos), Qwen3-8B SFT |

The pieces individually exist. The contribution is the **unified pipeline** + the **recall mechanism** + the **agentic-domain dataset**.

## Two key research questions this paper answers

1. **Does the dual-information channel from Memento survive in the agent setting?** Memento showed that retaining KV from masked thinking blocks gives 15pp accuracy on AIME24 *because the model produced those tokens during generation, leaving learned representations*. In agent loops the masked content (tool obs) was never *generated* — it was *prefilled*. Open question whether the implicit channel still helps.
2. **What's the right recall trigger for offloaded KV?** LRU is dumb. Embedding similarity over offloaded slice summaries is better. The model's own attention to mementos is best — but requires a forward pass to measure. We compare these head-to-head on agent benchmarks where re-visitation is real.

## Method (sketch)

```
agent step k:
  prompt = [system][user][asst_1<memento_1>][asst_2<memento_2>] … [asst_{k-1}<memento_{k-1}>][tool_{k-1}]
                       └── tool_1 KV offloaded to CPU
                                              └── tool_2 KV offloaded
  model generates: asst_k content + <memento_k>compressed summary of tool_{k-1}'s relevance to my plan</memento_k>

between step k and k+1:
  - parse <memento_k>, register as a "recall handle" pointing to tool_{k-1}'s offloaded KV
  - if attention from tail of asst_k to <memento_j> exceeds threshold for some j → speculatively prefetch tool_j's KV
  - prefetch happens during the tool execution (so latency is hidden)

step k+1:
  prompt assembled. If tool_j was prefetched, it's reinserted at its original position. KV slot in cache.
  model generates next turn.
```

The recall handle = explicit textual marker in the memento (e.g., `<memento idx=7>...</memento>`) that the masking layer maps back to KV blocks.

## Datasets

- **OpenAgentMementos** (we build this) — Hermes traces + SWE-Gym trajectories + agentic SWE-bench traces, segmented and annotated with mementos using the same iterative-judge recipe Memento used for OpenMementos. Target: ~50K traces. Will release publicly.
- **Evaluation benchmarks**: longdoc (Paper 1's, extended with multi-phase tasks that force re-visitation), τ-bench airline + retail, SWE-bench Verified.

## Models

- **SFT target**: Qwen3-8B (good size; matches Memento's smallest tested model)
- **Optional larger**: Qwen3-32B if compute permits
- **Baselines** (from Paper 1): `none`, `naive_summary`, `prefix_preserving`, `llm_reorganizer`
- **Reference**: Memento-style intra-call compression (not directly comparable but reported)

## Phased plan (6 phases, including Phase 0 as the Paper-1-to-Paper-2 bridge)

### Phase 0 — Action-graph supersession + structured consolidation log (NEW, ~1 week, no training/no vLLM mods)

**Goal:** demonstrate a training-free, no-vLLM-modification compaction policy that *measurably* beats `none` on at least one Phase C cell. This is the credibility-building step before the heavy infrastructure work, AND a useful baseline for the eventual Paper 2 figures.

The mechanism (chosen over heuristic eviction because Phase C showed heuristics don't win):

1. **Action-graph supersession** (Step A — see implementation details below).
   The agent's own subsequent tool calls reveal what's exhaust. After `edit_file(X, ...)`, the prior `read_file(X)` content is stale (file state changed). After the agent reads file Y from a `search(P)` result, that search hit is consumed. After a fresh `run_tests`, prior `run_tests` outputs are superseded. Build a small DAG tracker that runs every step (no LLM, no API cost) and tags obs with `_consumed=True` metadata. Eviction at compaction time drops only consumed obs. If nothing's consumed, don't compact (tie `none`).

2. **Structured consolidation log** (Step B, builds on A).
   Before dropping a consumed obs, append a structured one-line entry to a "knowledge log" message at a fixed early position in the trajectory. Format: `step K: <tool_name>(<args_summary>) → <consumed_at_step_M> [key fact extracted from obs]`. Append-only, deterministic, cheap (no LLM call — facts come from the original obs's structural fields plus a short content snippet).

Specifically, **Step A is action-graph-only (no log), Step B adds the log**. Ship them as two policies (`consumption_evict` and `consumption_evict_with_log`) so the ablation is clean.

**Concrete deliverables:**
- `studies/lifetime_cost/pipeline/policies/consumption_evict.py` — Step A
- `studies/lifetime_cost/pipeline/policies/consumption_evict_with_log.py` — Step B
- `configs/phase_d_v0_consumption.yaml` — eval on the 4 SWE-bench Lite tasks at Haiku
- Multi-seed run (3 seeds × 4 tasks × 4 policies) so the result is robust
- Updated `FINDINGS.md` with the verdict: did Step A beat `none`? did Step B?

**Implementation spec for the next Claude (so this can be picked up cold):**

The consumption-graph rules to implement, in `consumption_evict.py`:

```
For each tool message at position i, walk forward through messages and tag
m._consumed=True if any of these fire:

  (a) Same-target edit: a later assistant tool_call has name='edit_file' and
      args.path == this msg's source path. The edited file's earlier read is
      now stale wrt current file state.

  (b) Search-then-read: this msg is a 'search' result, AND a later tool_call
      reads at least one of the file paths that appeared in this msg's content.

  (c) Run-tests cascade: this msg is 'run_tests' output, AND a later tool_call
      is also 'run_tests'. Keep only the most recent test result.

  (d) List-files consumed: this msg is 'list_files' output, AND a later
      tool_call reads any file under the listed directory.

  (e) Duplicate read: this msg is 'read_file(X)', AND a later 'read_file(X)'
      for the same path exists. Keep only the most recent read of any file.

For each tagged msg, do NOT immediately drop. Instead, accumulate into a
"pending eviction" set. At each step, check if total bytes-tagged exceeds
`drop_threshold_tokens` (default 5000). When it does, drop all tagged msgs
in one compaction event (single cliff, justified by ≥5K bytes of confirmed
exhaust). If the budget never bites enough, the tagged msgs stay (no harm).
```

For Step B (`consumption_evict_with_log.py`), add: when a msg is tagged as consumed, before holing it, append to the running log message a one-liner derived from the msg's structural fields:
- `read_file(path) → list of (function_name, line_no) extracted via regex`
- `search(pattern) → top 3 matches verbatim`
- `edit_file(path, old, new) → summary of what changed (first/last 100 chars of new)`
- `run_tests(path) → exit code + first 200 chars of output`

The log message lives at a stable position (immediately after the system prompt, as a `user` role with a special prefix `[knowledge_log]`). Append-only → byte-stable through Step B's lifetime → cache hits stay.

Why Step B might beat Step A: when consumed obs are dropped, the agent loses the *fact* that it was learned (just the structural marker remains). Step B preserves the fact in the knowledge log. So the agent's mental model is intact even after aggressive eviction.

**Success criterion for Phase 0:** at multi-seed (3+ seeds × 4 tasks), Step A or Step B Pareto-dominates `none` on `cost_per_resolved` at Haiku, p < 0.05 by paired t-test on per-task cost. If neither does, the empirical motivation for Paper 2 is "even action-graph supersession can't escape the cliff" — which is also publishable as a negative result and even cleaner motivation for the KV-pointer approach.

**Status (2026-04-28 evening, single-seed):** Step A is built (`pipeline/policies/consumption_evict.py`) and shows initial promise — phase_d_v1_consumption_lazy run resolved 3/4 tasks (vs none's 2/4) at $1.03/resolved (vs none's $1.20), both Pareto-frontier alongside smart_evict. Most importantly, it resolved `pytest-7490` which no other policy has resolved across 8 smokes. Multi-seed validation is the immediate next step. Step B (knowledge log) is not yet implemented; would be the second commit toward Paper 2.

### Phase 1 — Build OpenAgentMementos dataset (3-4 weeks)
- [ ] Adapt Memento's pipeline (boundary scoring + segmentation + iterative summarization) for agent traces
- [ ] Run on Hermes 14K traces + SWE-Gym 2.4K (where the trajectories are reconstructible) + AC if useful
- [ ] LLM-judge-refine each memento (target: GPT-5 as judge, ~$200 of API)
- [ ] Validate on a held-out test split: do mementos preserve key information for downstream reasoning?
- [ ] **Release**: dataset to HF, code to GitHub

### Phase 2 — SFT a model (2-3 weeks)
- [ ] Two-stage SFT on Qwen3-8B (Memento-style)
  - Stage 1: full attention, learn to emit `<memento>` blocks at turn end
  - Stage 2: mask prior tool obs after memento; learn to operate without raw obs in attention
- [ ] Training infra: existing TRL/Axolotl/etc., 32 H100s for ~24h (modest)
- [ ] **Release**: model weights to HF

### Phase 3 — Inference infrastructure (3-4 weeks) — **the new engineering**
- [ ] **KV offload tier**: extend vLLM's prefix caching to push evicted blocks to CPU pinned memory + NVMe (LMCache exists but failed for us; either fix it or build minimal custom). For Paper 2, we just need a working "evicted blocks go to NVMe, can be pulled back" path. ~2 weeks.
- [ ] **Recall mechanism**: when the agent's next prompt contains a memento, the recall logic decides whether to fetch its underlying KV. Three triggers compared:
  - LRU (dumb baseline)
  - Embedding-similarity (memento embedding vs current query)
  - Attention-driven speculative prefetch (run a small probe forward pass, measure attention to memento markers)
- [ ] **Speculative prefetch**: kick off the recall during tool execution latency (which we can measure — typically 1-30s per tool call in agent traces) so the next LLM call doesn't pay the recall RTT
- [ ] **Memento-as-pointer protocol**: define the wire format (`<memento idx=N hash=X>`) and the binding from memento ID → offloaded KV chunks
- [ ] *Optional*: integrate Memento's vLLM fork for the mask-with-retain pattern (one specific ablation)

### Phase 4 — Evaluation (3-4 weeks)
- [ ] longdoc-multiphase (forces revisitation): N=50 × 3 policies × all triggers
- [ ] τ-bench: N=50 × 3 policies × all triggers
- [ ] SWE-bench Verified: N=30 × 3 policies (no triggers compared, just main method) × N=3 seeds
- [ ] Compare to Paper 1's best policies (already collected)
- [ ] Compare to baseline KV offload with LRU recall (no semantic recall)
- [ ] Report:
  - Lifetime cost
  - Resolve rate
  - Recall accuracy (recall@k for the slices the model would have benefited from)
  - Wall-clock latency (the speculative prefetch should make this win)

### Phase 5 — Writing + ablations (4-6 weeks)
- [ ] Draft
- [ ] Ablations:
  - Trained-emission vs LLM-reorganizer (does training help?)
  - With vs without speculative prefetch (does timing matter?)
  - With vs without dual-channel KV retention (Memento ablation, agent setting)
  - Memento granularity: per-turn vs per-N-turn
- [ ] Submission

## Headline figures (planned)

1. **Cost-vs-quality Pareto** — including the offload-recall systems vs Paper 1's best
2. **Latency** — wall-clock per agent step, with/without speculative prefetch
3. **Recall accuracy** — over time, how often does the recall mechanism bring back KV that the model actually needs
4. **Dual-channel ablation** — Memento's KV-retention finding tested in agent setting (predict: smaller effect than 15pp from Memento, because tool obs were never in generation)
5. **Compression ratio** — agentic-Memento vs Memento on the same model size (predict: similar 5-10× because Hermes tool obs are highly redundant)

## What we'd need that we don't currently have

- **Training compute** — ~32 H100s for ~24h (Memento used 32 B200s for similar setup)
- **API budget** — ~$500-1000 for OpenAgentMementos generation (frontier model summarization)
- **vLLM expertise** — KV offload tier engineering is non-trivial; either we build it or use Memento's fork as a starting point
- **Optional**: collaboration with NVIDIA Dynamo team if they're interested in the policy-on-top story

## Why not just use Memento's vLLM fork

Their fork gives us *mask with KV retention*. Useful for one ablation. But:
- They mask tokens generated within one forward pass; we mask tokens prefilled across turns
- They don't have an *offload* mechanism (KV stays on GPU, just masked)
- They don't have a *recall* mechanism (no need — the masked tokens are gone forever)
- They have it because RL training requires within-generation sequence-dependent masking; we don't have this constraint

The right move: borrow their masking primitive for one ablation, but build our offload+recall infrastructure on top of vanilla vLLM (cleaner, no dependency, easier to ship).

## Risks (named, with mitigations)

| Risk | Likelihood | Mitigation |
|---|---|---|
| KV offload tier engineering takes longer than 4 weeks | high | scope to "CPU only, no NVMe" if needed; LMCache as fallback |
| Recall trigger accuracy is low → recalled KV doesn't match what model needs | medium | start with the dumb LRU baseline; iterate triggers; can fall back to "always recall most-recent N evicted" |
| OpenAgentMementos quality is poor → SFT doesn't learn good mementos | medium | use Memento's iterative-judge recipe verbatim; budget more refinement iterations |
| The dual-channel hypothesis fails (KV retention doesn't help in agent setting) | medium | report as a clean negative result; that's still publishable |
| Compute for training Qwen3-8B SFT is unavailable | medium | drop to Qwen3-1.7B; Memento showed it generalizes across scales |

## Sequencing relative to Paper 1

- Paper 1 finishes (8 weeks): we have measurement, baselines, runner infra, longdoc/τ-bench eval working
- Paper 2 builds on this: most of the eval infra is reused; the new piece is the inference stack and the SFT
- Paper 2 also has Paper 1's results as a baseline to beat — sets the bar at "≥ 20% lifetime cost reduction over `llm_reorganizer` at equal resolve rate"

## Tracked-but-deferred from Paper 2 (would be Paper 3 or future work)

- Cross-agent KV sharing (subagent / swarm setups; cite Dynamo)
- Dynamic memento granularity (model decides when to emit, not fixed per-turn)
- Multi-modal mementos (image/audio outputs in agent loops)
- Online RL fine-tuning of memento emission
