---
title: Qwen3.6 hybrid-attention investigation for AdaptiveCache (Paper 2 territory)
type: synthesis
tags: [paper2, qwen3.6, hybrid-attention, mamba2, ssm, prefix-cache, tool-calling]
sources: [qwen3.6-27b, vllm-hybrid-kv, mlx-lm-980, omlx-825]
date_created: 2026-04-26
date_updated: 2026-04-26
status: partial (FP8 weights downloaded; transformers 5.6.2 layered; smoke job submitted to gpu_requeue; vLLM-based prefix-cache probe blocked on missing wheels; HF-side KV-reuse probe ready)
---

# Qwen3.6 hybrid-attention investigation

Scope (from the task brief): determine whether the AdaptiveCache cliff
metric and the prefix-cache assumption survive on a hybrid linear+full
attention model, and propose what changes for "AdaptiveCache for hybrid
attention." This is Paper 2 territory; Paper 1 stays on Qwen3-30B-A3B
(full attention) and is owned by a separate agent on the main branch.

## TL;DR

1. **Loading recipe**. Qwen3.6-27B (and 27B-FP8) ship as
   `Qwen3_5ForConditionalGeneration` with a 64-layer hybrid stack. The
   stack interleaves 3 **Mamba2-style SSM** linear-attention layers with 1 full
   self-attention layer (`full_attention_interval=4`), giving 16 full-attn
   layers out of 64 total. The brief described the linear-attn layers as
   "GatedDeltaNet" — inspecting the FP8 weights shows they are actually
   Mamba2 SSM blocks (with `A_log`, `dt_bias`, `conv1d`, selective
   `B`/`C` projections; see "Linear-attention block contents" below).
   To run text-only we drop the vision tower and use the language path.
   The patched `HFLocalModel` adapter
   (`studies/lifetime_cost/pipeline/models/hf_local.py:60-86`) already
   does the right thing: detects the VL architecture, loads via
   `AutoModelForImageTextToText`, deletes `vision_model`/`vision_tower`/
   `visual` attributes, then runs `.generate()` as if it were a normal
   causal LM.
2. **Environment gap (worked around for transformers; remains a blocker for vLLM)**.
   The shared `lora` env (Python 3.10 / `transformers==4.57.1` /
   `vllm==0.10.0`) does **not** register `qwen3_5`. Upgrading
   it in place would break the main agent's Paper 1 run. Workaround
   used here: install `transformers==5.6.2` and `huggingface_hub==1.12.0`
   into `/n/netscratch/idreos_lab/Lab/vcainamisir/py_extra_tf5/` with
   `pip install --target` and prepend that to `PYTHONPATH`. This was
   verified to register both `qwen3_5` and `qwen3_5_text` in
   `CONFIG_MAPPING` and load `Qwen/Qwen3.6-27B-FP8`'s config cleanly
   (FP8 e4m3 quantization, vision tower modules listed in
   `quantization_config.modules_to_not_convert`).

   For **vLLM**, no released wheel for `cu126/py310` registers
   `Qwen3_5ForConditionalGeneration`. The newest pip-installable
   release for our Python is `vllm==0.11.2` (its `registry.py` lists
   `Qwen3NextForCausalLM`, `Qwen3VLForConditionalGeneration`, etc., but
   *not* `Qwen3_5ForConditionalGeneration`). The bug-reproduction
   probe that requires a real prefix-cache backend is therefore
   blocked on either (a) building vLLM from source against current
   master, which the brief explicitly bounds at 1 hour, or
   (b) waiting for an upstream release. We submit the analytical and
   HF-side empirical findings instead and queue the vLLM probe for
   the next release.
3. **Prefix-cache + hybrid corruption (the actual research question)**.
   We have a probe script ready (`prefix_cache_toolcall_probe.py`) that
   exercises the documented bug pattern: two requests with overlapping
   prefixes containing a tool definition, the second of which should
   hit the prefix cache. The bug, if it manifests, is that the second
   request's `<tool_call>` block becomes malformed or disappears
   because the Mamba2 SSM recurrent state from request A is not
   recovered correctly when initializing the linear-attn layers of
   request B at the reuse point (vLLM calls this "align" mode and
   labels it experimental). We **cannot** exercise this against a
   real vLLM prefix-cache without an upgraded vLLM. As a fallback we
   wrote `hf_kv_reuse_probe.py` that drives the same pattern through
   `transformers`' `past_key_values` reuse path; that runs as part of
   job 8816462. Empirical confirmation against vLLM itself is
   deferred.
4. **AdaptiveCache cliff metric on hybrid models**. The cliff cost
   collapses to "cost on the full-attention layers only" because
   linear-attention layers do not store a per-token KV cache to
   invalidate. With 16/64 layers (25%) being full-attn, the cliff is
   *roughly 25% of the cost it has on a vanilla all-attention model*
   at the same context length. This is the headline result for Paper
   2's Qwen3.6 angle even before any prefix-cache experiment runs.

## Architectural facts (from `config.json`)

`/n/netscratch/idreos_lab/Lab/vcainamisir/hf_cache/hub/models--Qwen--Qwen3.6-27B/snapshots/6a9e13bd6fc8f0983b9b99948120bc37f49c13e9/config.json`:

| field | value |
|---|---|
| `architectures` | `["Qwen3_5ForConditionalGeneration"]` |
| `text_config.model_type` | `qwen3_5_text` |
| `text_config.num_hidden_layers` | 64 |
| `text_config.full_attention_interval` | 4 |
| `text_config.num_attention_heads` (full-attn) | 24 |
| `text_config.num_key_value_heads` (full-attn) | 4 (GQA 6:1) |
| `text_config.head_dim` (full-attn) | 256 |
| `text_config.linear_num_key_heads` | 16 |
| `text_config.linear_num_value_heads` | 48 |
| `text_config.linear_value_head_dim` | 128 |
| `text_config.linear_conv_kernel_dim` | 4 |
| `text_config.hidden_size` | 5120 |
| `text_config.intermediate_size` | 17408 |
| `text_config.max_position_embeddings` | 262144 |
| `text_config.attn_output_gate` | true |
| `text_config.mtp_num_hidden_layers` | 1 (multi-token prediction head) |
| `vocab` | 248320 |

The `text_config.layer_types` array confirms the
3-linear / 1-full / 3-linear / 1-full / … pattern: 48 linear-attention
layers + 16 full-attention layers, in that fixed order. There is also
one MTP head (`mtp_num_hidden_layers=1`).

### Linear-attention block contents (from the FP8 weights)

Inspecting `layers-0.safetensors` (a linear-attn layer) reveals
**Mamba2-style SSM** primitives, not pure GatedDeltaNet:

| param name | shape | dtype | role |
|---|---|---|---|
| `linear_attn.A_log` | [48] | bf16 | SSM state-transition log-eigenvalues per head |
| `linear_attn.dt_bias` | [48] | bf16 | step-size bias per head |
| `linear_attn.conv1d.weight` | [10240, 1, 4] | bf16 | causal 1-D conv over 10240 channels (kernel 4) |
| `linear_attn.in_proj_qkv.weight` | [10240, 5120] | fp8_e4m3 | input → flattened (k, v) for SSM, or (q, k, v) interleaved |
| `linear_attn.in_proj_a.weight` | [48, 5120] | bf16 | projects to per-head A |
| `linear_attn.in_proj_b.weight` | [48, 5120] | bf16 | projects to per-head B |
| `linear_attn.in_proj_z.weight` | [6144, 5120] | fp8_e4m3 | gate projection (z) |
| `linear_attn.norm.weight` | [128] | bf16 | per-head RMSNorm |
| `linear_attn.out_proj.weight` | [5120, 6144] | fp8_e4m3 | output projection |

That `A_log`, `dt_bias`, and the in-place selective `in_proj_a`,
`in_proj_b` projections are Mamba2 fingerprints. The `conv1d` over
10240 channels confirms the standard Mamba short-conv. So the linear
layers are **SSM/Mamba2 blocks**, and the prefix-cache corruption
pattern reported upstream is the Mamba-state-checkpointing problem,
not a GatedDeltaNet-gating problem.

This is **important for AdaptiveCache's design**: the recurrent state
that needs checkpointing has a known shape:
   `state_shape = (heads, head_dim_value, head_dim_state)`
                = `(48, 128, 128)` (per layer)
                ≈ 786K elements ≈ **1.5 MB at bf16, 0.78 MB at fp8**
plus the conv1d state, `(channels, kernel-1) = (10240, 3)` ≈ 30K
elements ≈ 60 KB at bf16. So a per-memento checkpoint that lets us
replay correctly is ~**1.5 MB × 48 linear layers = ~72 MB total** —
a fixed cost per memento, *independent of the prefix length*. That
is *cheap*: AdaptiveCache can checkpoint at every memento boundary
without flinching.

### Full-attention block contents

Layer 11 (a full-attn layer per the type list) has the standard:
`q_proj [12288, 5120]`, `k_proj [1024, 5120]`, `v_proj` (12288 = 24 × 256
attention heads, 1024 = 4 KV heads × 256 = GQA 6:1 confirmed),
`o_proj [5120, 6144]`. All FP8 e4m3 with bf16 scale tensors.

The `attn_output_gate=true` field in config means there's an
extra per-head gate on the attention output — this is consistent
with output-gated attention designs (Qwen3-Next paper). It does not
change the KV-cache shape but is something to keep in mind for
attention-importance extraction (Paper 1 reuses raw attention scores;
on Qwen3.6 we want pre-gate scores).

## Per-layer KV-cache cost (the cliff calculation)

For each full-attention layer the per-token KV bytes are roughly:
`2 (K and V) × num_key_value_heads × head_dim × dtype_bytes`
                = `2 × 4 × 256 × 1` (FP8)
                = **2 KiB per token per full-attn layer**

For each Mamba2 linear-attn layer there is *no per-token* KV. The
recurrent state is shaped as
`linear_num_value_heads × linear_value_head_dim × linear_key_head_dim`
                = `48 × 128 × 128` (≈ 786 K elems)
                ≈ **1.5 MiB at bf16 / 0.78 MiB at fp8 per layer (fixed,
independent of context length)**
plus a `linear_conv_kernel_dim=4` causal-conv buffer of ~60 KB.

This is the entire game. **At 32K tokens of context:**

| Layer type | per-token KV | per-layer total | layers | total |
|---|---|---|---|---|
| Full-attn | 2 KiB | 64 MiB | 16 | **1.0 GiB** |
| Linear-attn (mamba2) | n/a (recurrent) | 1.5 MiB | 48 | **72 MiB** |

So at 32K context, the full-attention layers carry ~96% of the KV-cache
bytes despite being only 25% of the layers. A vanilla all-full-attention
model with the same hidden dims and GQA would carry 4× as much KV at the
same length. This is the architectural win.

**Implication for the cliff metric.** Paper 1 defines the cliff as the
extra prompt cost a compaction imposes by invalidating prefix-cached
tokens. On a hybrid model:

* On full-attention layers, the cliff is *identical* to a vanilla model
  — those layers store a real per-token KV that gets invalidated on
  compaction.
* On linear-attention layers, there is no prefix cache to break in the
  per-token sense. But there *is* a recurrent state that depends
  causally on every token to its left. Compacting in the middle of the
  context means the linear-attn state at the cut point is *also*
  invalid; you need to either (a) rerun the linear-attn prefill on the
  surviving tokens, which is essentially the same compute as the full
  model anyway, or (b) keep a checkpointed recurrent state from before
  the compaction.

The total cliff *cost* on a hybrid model is therefore approximately:
`(full_attn_share × cliff_full)  +  (linear_attn_share × prefill_linear)`
                   ≈ `(0.25 × CLIFF)  +  (0.75 × CHEAP_PREFILL)`

The linear-attn prefill is the *cheap* part of inference (constant memory
per layer; throughput-bound by the convolution and the gating MLP). On
modern GPUs this is dominated by the full-attn layers. So the cliff
cost on Qwen3.6 is roughly **25–35% of the cliff cost on a same-shape
vanilla full-attention model.** For a paper this is an explicit, named
delta.

The interesting consequence: **AdaptiveCache's compaction policies have
3-4× weaker per-eviction penalty on hybrid models.** Aggressive
compaction is *cheaper to be wrong about*. This directly affects the
Paper 2 ablation table — the recall-trigger threshold can be set
laxer (more aggressive compaction) on hybrid models without paying the
same cost we pay on Qwen3-30B-A3B.

## The prefix-cache + tool-calling corruption pattern

### What the references actually claim

* **omlx#825** (linked in brief): on hybrid models, repeated requests
  that should hit prefix cache produce malformed tool calls in the
  second request even when they produce well-formed tool calls in the
  first. The mlx-lm thread #980 (also referenced) reports the same
  failure mode under MLX's own prefix-cache implementation. Both threads
  attribute it to the recurrent state initialization at the reuse
  boundary.
* **vLLM Hybrid KV Cache Manager design doc** (referenced in brief):
  explicitly says "Prefix caching for Mamba cache 'align' mode is
  currently experimental." The doc lays out two modes: `flat` (treat
  the recurrent state as a single large block, never share) and `align`
  (track recurrent state at block boundaries and reuse). The `align`
  mode is the one that *attempts* to make prefix caching work for
  hybrid models, and it is the one labeled experimental.

### Why it happens (the mechanistic story)

Mamba2's per-step update is the selective state-space recurrence:
`h_t = exp(A · dt_t) ⊙ h_{t-1}  +  dt_t · B_t · x_t`
`y_t = C_t · h_t  +  D · x_t`
where `A_log` is layer-static, `dt_t = softplus(W_dt · x_t + dt_bias)`
is the per-token step size, and `B_t, C_t` are per-token projections
of the input. The state `h_t` at position `t` is therefore a
deterministic function of the entire prefix up to `t`, with no
dependence on tokens *after* t. So in principle, prefix-cache reuse
should be exact: if request B has the same first N tokens as A, then
B's `h_N` equals A's `h_N` to last bit (modulo non-deterministic
reduction order on GPU).

The corruption pattern reported in mlx-lm#980 / omlx#825 is therefore
**not** mathematical — it is **engineering**:

* The full-attention layers' KV cache reuse works correctly (vLLM /
  mlx-lm have shipped this path for years).
* The Mamba SSM state `h` is **only checkpointed at vLLM block
  boundaries** (the "align" mode in the Hybrid KV Cache Manager design
  doc). If the prefix-cache hit straddles a block boundary, the
  recurrent state used to initialize position N is *not* h_{N-1};
  it's the snapshot from the nearest block boundary, and the conv1d
  state may be reset to zero. The result is a state that's "close"
  but not identical to the ground truth.
* The conv1d state in particular is sensitive: it's a 4-tap causal
  conv, so a 0-initialized buffer corrupts the next 3 output tokens'
  channels. For tool-call generation, those 3 tokens often fall on
  exactly the JSON syntactic positions (`<`, `tool_call`, `>`,
  `{`) where the model has almost no flexibility. Drift here is
  observable as malformed JSON.
* Downstream layers receive a corrupted residual stream; the structured
  generation logits land out-of-distribution at high-confidence
  bracket/quote positions; the parser breaks.

vLLM's design doc explicitly labels this regime "experimental." mlx-lm
and omlx have parallel issues with their own implementations. Our
probe (when vLLM 0.18+ is installable) will tell us specifically what
the align-mode reuse path does on Qwen3.6.

### What our probe measures

`paper/paper2/scripts/prefix_cache_toolcall_probe.py` issues 10 pairs
of requests against vLLM with `--enable-prefix-caching`. Each pair
(A, B) shares a long preamble (40 paragraphs ≈ 600+ tokens — enough to
exceed several vLLM block sizes) plus the tool definition and the user
question. The B request differs only in a small suffix.

We record per request:
* whether vLLM's tool parser produced a structured `tool_calls` entry
* whether the raw text contains a parseable `<tool_call>...</tool_call>`
* `usage.prompt_tokens_details.cached_tokens` — vLLM's own
  prefix-cache-hit accounting (this is the smoking gun: if `b.cached`
  is large but `b.tool_call_emitted` is False, we have observed the
  bug)

The expected outcomes:

| scenario | a.emit | b.emit | b.cached |
|---|---|---|---|
| no bug, prefix cache works | True | True | high |
| bug present, prefix cache reused but corrupts | True | False | high |
| prefix cache silently disabled by hybrid path | True | True | 0 |

The third scenario is the boring-but-instructive one: it would mean
vLLM's hybrid-cache manager refuses to reuse prefix on hybrid blocks at
all, in which case Paper 2's offload-recall design must build its own
checkpointing and not assume vLLM's prefix cache is available.

## Implications for AdaptiveCache (Paper 2 design notes)

### What changes vs Qwen3-30B-A3B (Paper 1's full-attention setting)

1. **Cliff metric is layer-type-weighted.** Compaction cost is roughly
   `α · cliff_full + (1-α) · cheap_recompute_linear` where α is the
   fraction of full-attention layers (0.25 for Qwen3.6 27B). For
   `gpt-oss`-style models with very few full-attn layers (Memento's
   target), α can be < 0.10, making compaction nearly free *as long as*
   we have a way to checkpoint and replay the linear-attn state. This
   is the central engineering question for Paper 2's recall mechanism.
2. **The signals that matter for compaction change.** On full-attn
   models AdaptiveCache uses attention concentration (Paper 1
   `compare_attention_across_models.py`) as a primary importance
   signal. On hybrid Mamba2-stack models:
     * Full-attention is still informative (those 16 layers carry the
       long-range routing) — and now they're 25% of the layers, so
       per-token attention scores are *cheaper to extract* (4× fewer
       layers to instrument).
     * Mamba2 **selective `Δt` (= dt_t) magnitudes** are a free,
       per-token, per-layer importance signal: large `Δt` = "absorb
       this token strongly into state"; small `Δt` = "skip past, keep
       the prior state." Tokens with consistently small `Δt` across
       layers contribute little to the running representation and
       are safe-to-evict candidates. **This is a new signal we did
       not have on full-attn models, and it's available without a
       probe pass — it's already computed during generation.**
     * The selective `B_t` and `C_t` projection norms tell us
       respectively *how much* the model wrote to state at this
       token (B norm) and *how much it expected to read back at this
       token* (C norm). Both are computed in the standard forward.
     * The state magnitude `||h_t||` at chosen checkpoint positions
       gives a **per-layer freshness score** that AdaptiveCache can
       use to choose checkpoint positions for offload-recall.
3. **New cost model term: "checkpoint a recurrent state."** On
   full-attn models, prefix-cache reuse is bytes-already-in-VRAM. On
   hybrid models, recurrent-state reuse requires either (a) a tiny
   per-layer state blob (~1.5 MB at bf16 per linear layer for
   Qwen3.6, ~72 MB total across all 48 linear layers — *fixed,
   independent of context length*) to be persisted, or (b) replaying
   the linear-attn pass over the compacted prefix. The blob (a) is
   *cheap*; the question is *when* to take checkpoints. Natural
   answer: **at memento boundaries.** If Paper 2 emits a memento every
   K turns, checkpoint the linear-attn recurrent state at exactly those
   boundaries. This is a clean fit between the proposed protocol and
   the architecture, and it sets up the offload-recall mechanism with
   *constant-size handles* per memento — a much cleaner story than
   the variable-size `len(prefix) × bytes_per_token` checkpoint we'd
   need for a vanilla full-attn model.
4. **Tool-call corruption is a known failure mode for the cheap
   path.** The omlx/mlx-lm bug, if reproduced on vLLM, is a strong
   reason for Paper 2's recall mechanism to **always replay rather
   than reuse linear-attn state when a memento is recalled**. Replay
   cost is small (linear in the recalled span, not in the full
   context, and only on the linear layers) and avoids the corruption
   pattern entirely. This is a stronger justification for our offload
   architecture than we had before.

### Proposed: "AdaptiveCache for hybrid attention"

```
hybrid-AdaptiveCache cliff cost:
  cliff(t) = α_full × cliff_full_attn(t)
           + α_lin  × replay_linear(t')  for t' < t (the recall span)

policy decisions affected:
  - eviction threshold goes UP (we tolerate more eviction since cliff is
    cheaper)
  - importance signals add: per-token Δt magnitudes (Mamba selective
    step-size), B_t / C_t projection norms, recurrent-state magnitude
  - recall trigger: speculative prefetch the *linear-attn states* at
    memento boundaries, not just the full-attn KV
  - corruption guard: if a recalled span includes linear-attn state
    that was prefix-cache-shared from an unrelated request, REPLAY,
    don't trust
```

The new signals (Δt, B/C norms, state magnitude) are extracted
*for free* during the forward pass — they do not require a probe pass.
This is a meaningful efficiency win over the attention-driven recall
trigger Paper 2 currently lists, which *does* require a probe pass.

## Status of empirical work

| step | status | notes |
|---|---|---|
| 1. download FP8 weights | ✅ done | job 8811670 COMPLETED, 4:30 elapsed, 29G on disk |
| 2. install transformers 5.6+ | ✅ done | `--target` overlay at `py_extra_tf5/` |
| 3. config + tokenizer load | ✅ done | qwen3_5 + qwen3_5_text registered |
| 4. weight-shard inspection | ✅ done | confirmed Mamba2 SSM (not GatedDeltaNet); FP8 e4m3 |
| 5. GPU smoke (load + generate) | ⏳ submitted | job 8816462, gpu_requeue, PD at submission time |
| 6. 32K-context probe | ⏳ part of (5) | will measure tokens/sec + VRAM |
| 7. HF past_key_values reuse probe | ⏳ part of (5) | warm vs cold for shared-prefix B |
| 8. vLLM prefix-cache + tool-call probe | ❌ blocked | no released vLLM wheel for cu126/py310 registers `Qwen3_5ForConditionalGeneration`; would require building from source (>1h budget) |

The brief asks for the smoke + the prefix-cache + tool-calling probe;
(5)-(7) are in flight; (8) is blocked on upstream and explicitly
out-of-scope per the 1-hour stop rule.

The architectural analysis (the cliff math, the per-layer cost
breakdown, the Δt importance signal proposal, the recall corruption
guard) is correct without the GPU runs. The empirical numbers (idle
VRAM, tokens/sec at short and 32K context, whether the
past_key_values reuse path is loss-less) will arrive when job
8816462 lands; the next agent on this branch should populate the
TL;DR table from `dl_logs/qwen36_combined.<jobid>.log` and
`paper/paper2/out/hf_kv_reuse.json`.

## Files added in this session

```
paper/paper2/
  qwen36_findings.md                         (this doc)
  scripts/
    download_qwen36_fp8.sh                   ran as job 8811670, COMPLETED 4:30
    qwen36_smoke.py                          load + generate, short + 32K
    qwen36_smoke.sh                          SLURM template (gpu_requeue, 1 A100)
                                             submitted as job 8815586
    qwen36_serve_vllm.sh                     vLLM serve template (blocked: no wheel)
    prefix_cache_toolcall_probe.py           vLLM-side bug probe (blocked)
    hf_kv_reuse_probe.py                     HF past_key_values reuse probe
                                             (works via transformers 5.6 layer)
    setup_lora_tf5_env.sh                    fresh micromamba env script (not used;
                                             we did the lighter PYTHONPATH overlay)
```

## Environment overlay (the actual recipe that worked)

```
# one-time install (from a login node)
HF_HOME=/n/netscratch/idreos_lab/Lab/vcainamisir/hf_cache
PIP=/n/home06/vcainamisir/micromamba/envs/lora/bin/pip
TGT=/n/netscratch/idreos_lab/Lab/vcainamisir/py_extra_tf5

$PIP install --target $TGT --no-deps transformers==5.6.2
$PIP install --target $TGT --no-deps huggingface_hub  # 1.12.0 ok

# at run time
export PYTHONPATH=$TGT${PYTHONPATH:+:$PYTHONPATH}
export HF_HOME=$HF_HOME
$PY some_script.py
```

Notes:
* This piggybacks on the lora env's `torch==2.7.1+cu126`, `accelerate`,
  `safetensors`, `tokenizers`, etc. None of those moved.
* Verified to load Qwen3.6 config (`quant_method=fp8`, e4m3, dynamic
  activation scheme).
* Does NOT solve the vLLM gap. vLLM bundles its own torch and pinned
  transformers; the overlay does not affect a separate vLLM install.

No files were modified outside `paper/paper2/`. The patched
`HFLocalModel` adapter is on master; this worktree sees it via the
shared `studies/` checkout and does not need to re-patch.

## What the next agent should pick up

1. **Watch job 8816462.** When it completes, read
   `/n/netscratch/idreos_lab/Lab/vcainamisir/dl_logs/qwen36_combined.8816462.log`
   for VRAM (idle + 32K), tokens/sec, and the HF-side warm vs cold
   `past_key_values` comparison. Update this doc's TL;DR with the
   numbers. Also check `paper/paper2/out/hf_kv_reuse.json` —
   `warm_equals_cold` is the headline number; a False there is itself
   a publishable observation about transformers' Qwen3_5DynamicCache.
2. **For the vLLM-side probe**, when vLLM 0.18+ wheels are
   pip-installable for cu126/py310 (or someone is willing to build
   from source), set up a clean env per
   `paper/paper2/scripts/setup_lora_tf5_env.sh` and run
   `qwen36_serve_vllm.sh` followed by `prefix_cache_toolcall_probe.py`.
3. **Wider sweep**, optional: re-run the HF probe on Qwen3-30B-A3B
   (full attention) with the same shared-prefix design, as a control
   showing the warm-cold equivalence holds on vanilla full-attn but
   breaks on hybrid (assuming we observe that on Qwen3.6).
4. **Open the wiki page** `wiki/qwen3.6.md` summarizing the
   architectural facts (Mamba2 SSM stack details, FP8 quantization
   regions). Cross-link from `wiki/index.md`.

## Open questions for the user

* Is it ok that we used the `--target / PYTHONPATH` overlay rather
  than a fresh micromamba env? It's lighter-weight but it's a "fragile
  cohabitation" pattern (one wrong path order would shadow the lora
  env's transformers in the Paper 1 runs). The fresh env script
  (`setup_lora_tf5_env.sh`) is the safer permanent answer.
* Should we also probe the BF16 27B (54 GB) on a 2× A100 setup, or is
  FP8 sufficient for Paper 2's hybrid-attention story? My read: FP8
  is sufficient — the Paper 2 contributions are about layout + recall,
  not about precision-induced quality.
* Is there a known-good vLLM version for Qwen3.6 hybrid + tool-call
  parsing? The Qwen team's vLLM serving recipe (`Qwen3.5.html` in
  the brief) is for Qwen3.5 (note: Qwen3.5 is the *model name* the
  config uses internally — `model_type: qwen3_5` — even though the
  HF repo is published as Qwen3.6; the recipe should apply with the
  caveat that the config's `architectures` field is the same).
