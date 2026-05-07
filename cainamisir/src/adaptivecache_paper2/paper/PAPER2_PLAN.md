# Paper 2 — Plan

## Working title

**Mementos as KV Pointers: Speculative Recall for Long-Running LLM Agents**

## One-line pitch

When an agent compacts away earlier context, the *bytes* are gone but the *KV cache* doesn't have to be — we offload it instead, leave a compressed memento behind as a textual pointer, and speculatively prefetch the offloaded KV when the model starts attending heavily to the memento. Best of both worlds: aggressive compaction (text-level summary always visible) + perfect quality recovery when the agent revisits earlier work.

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

## Phased plan (5 phases, 14-21 weeks)

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
