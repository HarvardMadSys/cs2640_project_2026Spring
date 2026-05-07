# Paper 2 — v2 Design: Append-Only Recall

Date: 2026-05-02. Author: Claude / Vlad. Status: design, not yet built.

## TL;DR

**v1 recall (already built)** un-elides an obs at its *original chronological
position*. Adding tokens in the middle shifts every suffix position by
`len(obs)`, so vLLM's prefix cache misses on the entire suffix → full
re-prefill of the post-recall context. Cost shape: same cliff as Paper 1
compaction events ($0.10–0.15 per recall on Qwen3-30B at our scales).

**v2 recall (this design)** appends the recalled obs *as an addendum at the
end of the prompt*. The chronologically-elided context (mementos in place
of obs, suffix unchanged) is preserved. The cache hash chain
extends — prefix cache hits everything before; only the appendix
prefills. Cost shape: a few hundred tokens prefill, ~$0.001.

The algorithm comes from taking the "stale KV / historical truth" insight
seriously: the suffix tokens were generated in *memento world* (obs elided);
re-prefilling them at new positions is *counterfactual*; **the agent really
did proceed memento-only and that history must be preserved**. Recall
extends the historical context with new information at the present moment,
not by rewriting the past.

## Position of v2 in the design space

| Variant | What it does | Cost shape | Preserves prefix cache? |
|---|---|---|---|
| v0 (current memento) | Compact obs at compaction time; never recall | Low (memento markers cheap) | Yes (post-compaction is stable) |
| v1 text-level recall | Un-elide obs at original chronological position | Cliff (~$0.10–0.15 per recall) | No — entire suffix shifts |
| **v2 append-only recall** | **Keep elision; append obs as addendum at end** | **Cheap (~$0.001 — only addendum prefills)** | **Yes — prefix unchanged** |
| v3 KV-level mask flip (theoretical) | Toggle attention bias; reuse pre-computed KV | $0 in principle | Requires custom vLLM patches; out of scope |

## Why we are NOT building v3

The "true Memento" mechanism (preserve obs KV in cache, flip an attention
mask, no re-prefill) lives **inside a single forward pass**. Across vLLM
`chat()` calls, the prefix cache lookup is hash-based on prompt token IDs.
The KV state from the previous call is matched only by hash, not by
out-of-band reuse. To force vLLM to re-use stored obs KV across calls
would require:

1. Patching the prefix-cache lookup to accept an explicit `(prompt, KV)`
   pair instead of `(prompt) → hash → KV`.
2. Patching the block manager to pin obs blocks across requests rather
   than recycle them.
3. Patching the attention kernel's `seq_lens` and `slot_mapping` to
   recombine pinned obs blocks with newly-prefilled suffix blocks at
   request time.

This is a large vLLM fork; not buildable at our scope. **v2 reaches the
same headline result (cheap recall, prefix preserved) at the prompt
layer instead of the KV layer.**

## v2 Algorithm

### Prompt structure

After compaction, normal chronological prompt looks like:

```
... earlier turns ...
[tool_response, evicted, memento]\n<memento_2 text>     <-- obs_2 elided
... assistant reasoning ...
[tool_response, evicted, memento]\n<memento_3 text>     <-- obs_3 elided
... assistant reasoning ...
<tool_response>obs_4</tool_response><|fim_prefix|>memento_4<|fim_middle|>
... assistant reasoning ...
<assistant turn N — current>
```

When recall fires for obs_2, v1 would replace `[tool_response, evicted,
memento]\n<memento_2 text>` with the full obs_2 text. v2 instead leaves
that block unchanged and **appends** to the end of the prompt:

```
... entire prompt above unchanged ...
<assistant turn N — current>
[recalled, obs_2]\n<full obs_2 content>     <-- NEW addendum
```

The next assistant turn is generated with this addendum visible. The
agent gets the obs back, the prompt prefix is unchanged, the prefix
cache hits everything up to the new addendum.

### Cache analysis

Let `T_pre` = tokens in prompt before recall. Let `T_obs` = tokens in
the recalled obs content. Then:

| Step | Cached tokens | Newly prefilled |
|---|---|---|
| Pre-recall chat() call | `T_pre - 1` (everything except last token) | 1 |
| Post-recall chat() call (v1) | `T_pre_shared` (smaller — shifts kill cache) | `T_pre - T_pre_shared + T_obs` |
| Post-recall chat() call (v2) | `T_pre - 1` (entire pre-recall prompt) | `1 + T_obs + addendum_marker` |

For our pytest-7490 trace at recall time: `T_pre ≈ 8000`, `T_obs ≈ 1200`,
addendum_marker ≈ 5 tokens. v1 prefills ~9200 tokens (cliff). v2
prefills ~1206 tokens (cheap).

### Position-encoding consideration

In v2, the recalled obs sits at logical position `T_pre + addendum_marker`,
not at its original chronological position. This means:

- The agent must understand that `[recalled, obs_2]` is the SAME content
  as the obs that was previously elided. The marker text plus a memento
  ID makes this explicit: `[recalled, obs_id=2, originally_at_step=3]`.
- Any chronological coreference (e.g. "the test I ran in step 3") still
  resolves through the suffix's own narrative, which references the
  obs via memento_2's summary text. The recalled content fills in the
  detail.

This is exactly the "addendum semantics" humans use — references in the
body, full text in an appendix.

### Multi-recall

Recall can fire multiple times. Each fire appends another `[recalled,
obs_k]` block. Order of appendices is recall order, not chronological
order. The cache analysis above composes: each recall extends the
prompt by `T_obs_k + marker`.

### Re-eviction

If the addendum stack grows too large, we evict from the addendum
rather than the chronological body. Eviction order: oldest addendum
first (treat the addendum as an LRU stack). The chronological body's
mementos never get un-elided.

## Implementation surface

All changes are in the policy + adapter layer. The Memento overlay does
NOT need patching. This is a v1.5-style change, not a v2-style infra
project.

### Files to modify

1. **`policy/memento_policy.py`** — `maybe_recall` returns
   `(messages, RecallEvent)` where `messages` is the original messages
   (unchanged) plus a new synthetic message at the end containing the
   recalled obs marker + content. The eliding rendering stays as-is.

2. **`adapters/memento_vllm.py`** — `transform_messages` already handles
   role=tool messages. Add handling for a new role=user message with a
   `recall_marker` flag, rendered as `[recalled, obs_id=N]\n<content>`
   plain text (no block markers — these tokens shouldn't fire compaction).

3. **`policy/recall_strategy.py`** — strategies stay the same (LRU,
   embedding). The decision of WHICH memento to recall doesn't change;
   only WHERE to insert it does.

4. **`pipeline/types.py` Message** — add `recall_marker: Optional[str]`
   slot for serializability. Trajectories should record the recalled obs
   so analysis can compare hit rates against ground-truth re-reads.

5. **No runner changes needed** — `maybe_recall` continues to fire
   before each chat() call, just appending to messages instead of
   in-place editing.

### Cache verification path

We don't need to instrument vLLM internals. The adapter already
computes `cached_tokens` via byte-prefix simulation
(`MementoVLLMModel.chat`, the `_prev_rendered` byte-walk). v2's
correctness shows up directly as: `cached_tokens` after a v2 recall
should be ≥ pre-recall prompt size; v1's would be much smaller. We can
A/B this on the SAME task by just toggling the recall variant.

## Acceptance criteria

1. **Cache preservation**: on a recall event, `cached_tokens` for the
   next chat() call is ≥ 95% of the pre-recall prompt size. (v1
   typically falls to <40% because of suffix shift.)

2. **Wall reduction**: total wall on a multi-recall task drops by ≥ 30%
   vs v1, holding resolve rate constant.

3. **Resolve parity**: on the pytest-7490 + multi-seed harness, v2
   resolve rate is within ±1 task of v1. (We don't expect v2 to resolve
   more — same information; just cheaper. If v2 resolves fewer, the
   addendum semantics aren't being understood by the agent and we need
   a different marker template.)

## Risk: agent comprehension of addendum semantics

The biggest unknown is whether Qwen3 will correctly merge addendum
content with chronological references. The marker template needs
testing. Candidate templates:

- `[recalled, obs_id=2, originally_at_step=3]\n<obs>`
- `<recalled_observation step="3">obs</recalled_observation>`
- `# Earlier observation (recalled)\n\n<obs>`

Pick by smoke test: render each on a known recall trace, ask the model
"what file did you read in step 3?", check if it resolves correctly.

## Out-of-scope but worth noting

- **v3 (KV-level mask flip)**: would still be the "free recall" prize.
  Requires either a custom vLLM fork or operating against a different
  inference engine (TensorRT-LLM, SGLang) that exposes lower-level KV
  access. Worth thinking about for Paper 3 / future work.

- **AdaptiveCache (Paper 1) integration**: v2's prompt structure (body
  + addendum stack) IS exactly the layout-aware design Paper 1 argues
  for. v2 is a concrete instance of "stable prefix, volatile tail."
  Worth flagging in the unified writeup as evidence the two papers
  share a thesis.

## Implementation plan (concrete)

1. Add `recall_marker` field to `Message` (5 min).
2. Modify `MementoPolicy.maybe_recall` to APPEND a synthetic addendum
   message instead of replacing in-place. Keep old in-place mode under
   a flag for v1-vs-v2 ablation (~30 min).
3. Modify `transform_messages` to render the new addendum message
   correctly (~15 min).
4. Add unit test: assert that after recall fires, the prompt prefix
   from `_prev_rendered` is preserved up to the addendum (~20 min).
5. Smoke run on pytest-7490 single seed; verify `cached_tokens` shape
   matches the table above (~30 min).
6. Multi-seed bake: same harness as v1, add `memento_v2` variant (~few
   hours wall, depending on GPU availability).

Total: ~1 day of engineering, then a multi-seed run.
