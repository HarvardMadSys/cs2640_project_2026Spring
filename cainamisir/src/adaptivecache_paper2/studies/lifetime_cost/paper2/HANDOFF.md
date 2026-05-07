# Paper 2 — Handoff (2026-04-28)

For the next session (or whoever picks this up cold). This is the working
file; `FINDINGS.md` and `FINDINGS_swebench.md` are the immutable
result snapshots.

## Where we are

v0 plumbing complete and validated end-to-end on a real SWE-bench Lite
task with Qwen3-30B-A3B. The mechanism works:

- Memento overlay's masking fires on real agent loops (9-15 compactions
  per task on the Phase-C 4-task set).
- Lazy policy: only generates mementos when context crosses a budget
  threshold. Most agent steps incur zero Haiku calls.
- On a fair 15-step swebench comparison: chat wall **-17.6%**, final
  prompt **-79%** vs baseline; baseline crashed at 32K context, memento
  ran clean.
- Quality preserved: on 3 easier tasks × 2 seeds × 2 variants, resolve
  rate is identical (2/6 each, both `pylint-5859` seeds).

Branch: `paper2-memento-recall` — `git log` since master is the commit
trail. Worktree at `/home/vlad/adaptivecache-paper2/`. Isolated venv at
`.venv-paper2/`. Read `README.md` for the stack details.

## What's actually validated

| Claim | How it's measured | Status |
|---|---|---|
| Memento overlay loads on Blackwell + CUDA 12.8 | `tests/smoke_memento_masking.py` | ✓ |
| Masking fires on real prompts (frees KV blocks) | smoke + per-step BlockMasking debug log on swebench | ✓ |
| Multi-turn doesn't cascade (was the 5× cliff) | `tests/microbench_masking.py` last_only_masking=True | ✓ — overhead 1.32× not 5× |
| Memento beats baseline at scale | `tests/microbench_growing.py` 20-turn | ✓ — -12% total wall, crossover at turn 5 |
| Real agent loop runs end-to-end | `v0_swebench.py` on psf__requests-3362 | ✓ — 15 steps, 10 compactions, no crash |
| Memento prevents context overflow | same task at 32K ctx — baseline crashed step 9, memento ran 15 | ✓ |
| Memento doesn't break resolve rate | 3 easier tasks × 2 seeds: 2/6 baseline = 2/6 memento | ✓ on small sample |

## Known broken / open

### A. Trajectory `messages_in` drops the `memento` field on serialization
`pipeline/types.Message` doesn't have a `memento` slot, so when the
runner builds Step.messages_in via `_message_kwargs(m)`, the attached
memento is lost. Runtime is unaffected (the dict goes through to next
chat call); only post-hoc analysis sees plain content. To fix: add
`memento: Optional[str] = None` to `Message` + persist it through
`_message_kwargs` and `Message.to_dict`. ~15 min fix.

### B. Re-reading pathology (Phase C v6 redux)
On `psf__requests-3362` the agent re-read `requests/models.py` 5 times
in a row in steps 1-5. Each read got mementoed and evicted; model
couldn't see the file content; re-read because the memento was too
generic. **This is exactly what the v1 recall mechanism is supposed to
fix.** See section "v1 plan" below.

### C. Haiku is on the chat() critical path
The lazy policy still synchronously waits for Haiku to return before
the runner advances. ~3-4s per memento. Total Haiku time on the
swebench run: 43.7s out of 53s wall. Two options:
1. **Replace Haiku with SFT'd Memento model** (the actual paper plan):
   memento generation becomes part of the agent's normal generation,
   adding zero extra wall.
2. **Async Haiku** in v0 only: kick off the call, return from
   maybe_compact immediately, attach memento on the *next* policy
   firing. Smaller-scope fix, useful while we don't have an SFT model.

### D. small-fix unit-test-resolve oracle is coarse
`swebench_live`'s resolve metric is git-diff overlap with the gold
patch, not actual test_patch execution. False positives + negatives
both exist. Fine for policy-vs-policy comparison; not fine for
absolute-resolve claims. Phase 0's `consumption_evict_with_log` work
on the master branch may have a better oracle once landed.

## v1 — Recall mechanism (the actual paper contribution)

The whole point of "Memento for Agents" beyond Memento itself is
**bidirectional masking**: when the model needs an evicted obs back, we
restore it instead of paying a re-read.

### Three triggers to compare (Paper 2 RQ2):

1. **LRU baseline** — when the next chat() prompt grows past trigger,
   restore the most-recently-evicted block. Trivial to implement; sets
   the floor.
2. **Embedding-similarity** — embed each memento's text once at
   compaction time. At each chat(), embed the recent assistant turn
   and the current user query. If cosine-sim to memento_j > threshold,
   pre-restore tool_j's obs into the next prompt. Needs a small
   embedding model (sentence-transformers MiniLM works fine, ~80MB).
3. **Attention-driven probe** — run a small forward pass on the
   pre-recall prompt, measure attention from the trailing tokens to
   each memento marker, restore the high-attention ones. Requires
   open-weights model (Qwen3 fits) and a hook into vLLM's attention
   forward. The novel piece.

### Implementation paths

- **Cheap path: text-level recall.** When the trigger fires, replace
  `[tool_response, evicted, memento]\nMEMENTO_TEXT` in the prompt with
  the original full obs. The engine sees this as a different prompt,
  prefix cache misses past the change, but the obs comes back. Cost: a
  re-prefill of the obs region. No vLLM changes needed.
- **Expensive path: KV-level recall.** Hold the evicted KV blocks in
  CPU pinned memory, restore them into vLLM's prefix cache when
  recall fires. No re-prefill. Requires patching the Memento overlay
  to track block IDs across requests.

I'd ship cheap path first, get end-to-end numbers, then attempt
expensive path as the publication contribution.

### Where to start
1. Add `MementoPolicy.maybe_recall(messages, ctx)` — separate from
   `maybe_compact`. Runner calls it before each chat() to decide
   whether to swap an inlined memento back to its full obs.
2. Track `(memento_id, original_obs)` per tool message — currently we
   only attach `memento` to the message; need to also keep the
   original `obs` (it's already there as `content`, just not flagged).
3. LRU first (~half day), then embedding-sim (~1 day with HF
   sentence-transformers), then attention-driven (~2-3 days, needs
   vLLM hook).

Acceptance criterion: on `psf__requests-3362`, the model should
re-read `requests/models.py` ≤2 times instead of 5+ times. Measure
this directly from the trajectory tool_calls.

## v2 — Real evaluation

Once recall lands, run a proper eval:
- 4-8 swebench Lite tasks (mix easy + hard)
- 3 seeds each, T=0.6
- 4 variants: `none`, `consumption_evict` (Phase 0), `memento`
  (current), `memento+recall`
- Report: resolve@k, GPU-seconds per resolved, prompt-tokens per
  resolved, recall accuracy (recall@k for the obs the model would
  have re-read)

Once Phase 0 is committed on master, rebase and re-run vs the new
swebench_replay if it has a better oracle.

## How to run things (quick reference)

### Smoke tests (no API key needed for the masking-only ones)
```
cd /home/vlad/adaptivecache-paper2
.venv-paper2/bin/python studies/lifetime_cost/paper2/tests/test_transform_messages.py        # unit tests
.venv-paper2/bin/python -m studies.lifetime_cost.paper2.tests.smoke_memento_masking          # GPU smoke
.venv-paper2/bin/python -m studies.lifetime_cost.paper2.tests.microbench_growing             # 10-turn microbench
PAPER2_N_TURNS=20 .venv-paper2/bin/python -m studies.lifetime_cost.paper2.tests.microbench_growing  # 20-turn
```

### Real swebench (needs ANTHROPIC_API_KEY for memento variant)
```
set -a && . /home/vlad/adaptivecache/.env && set +a
PAPER2_INSTANCES="pylint-dev__pylint-5859" PAPER2_N_SEEDS=2 PAPER2_TEMPERATURE=0.6 \
  .venv-paper2/bin/python -m studies.lifetime_cost.paper2.v0_swebench
```

Env vars:
- `PAPER2_MODEL` — defaults to Qwen3-30B-A3B-Instruct-2507
- `PAPER2_INSTANCES` — comma-separated swebench instance ids
- `PAPER2_N_SEEDS` — multi-seed (different RNG, same temperature)
- `PAPER2_TEMPERATURE` — 0.0 deterministic, 0.6 for variability
- `PAPER2_MAX_LEN` — vLLM `max_model_len`. 65K survives most baseline
  tasks; 128K is safe at 0.92 GPU util on the 96GB Blackwell.
- `PAPER2_GPU_UTIL` — default 0.92
- `PAPER2_MASKING` — "0", "1", or "both" (default)

### Find easier tasks
```
.venv-paper2/bin/python -m studies.lifetime_cost.paper2.find_easy_tasks
```

## Stack gotchas (from memory + this session)

1. **vLLM 0.13.0 default wheel ships an FA-2 .so for CUDA 13** — fails
   on driver 12.8 with `cudaErrorUnsupportedPtxVersion`. Force
   FlashInfer backend: `VLLM_ATTENTION_BACKEND=FLASHINFER`. The
   adapter sets this in its env defaults.
2. **`restart_mode=True` is required** for prompt-time compaction.
   Without it, the engine hangs forever in deferred compaction limbo.
3. **`keep_last_n_blocks > 0` deadlocks** in some configurations.
   Don't use it. Stick with 0 + last_only_masking rendering.
4. **Block markers reuse Qwen3's existing vocab tokens** (no SFT, no
   tokenizer mod): `<tool_response>` (151665) / `</tool_response>`
   (151666) for block_start/end, `<|fim_prefix|>` (151659) /
   `<|fim_middle|>` (151660) for summary_start/end. The FIM tokens
   are repurposed because Qwen3 doesn't naturally emit them in chat.
5. **Phantom-block cost**: any `<tool_response>` literal in user content
   gets tokenized as the special block_start ID, even without summary
   markers, and the masking processor counts it as a phantom block.
   `wrap_tool_message_for_masking(obs, None)` returns plain
   `[tool_response]\n{obs}` (no `<>`) to avoid this.
6. **`memento` field on tool messages** is the contract between the
   policy and the adapter. Adapter renders inlined plain text for
   older turns, full markers + summary for the most recent.

## Other Claude's Phase 0

There's a separate Claude working on master with Phase 0
(`consumption_evict`, `consumption_evict_with_log`). Not committed
yet. Once it lands:
- Rebase `paper2-memento-recall` onto the new master
- Add `consumption_evict` as a 4th variant in the v2 eval
- Combined story: text-side eviction (consumption_evict) +
  KV-side masking + recall (this work) = the full picture

The two are orthogonal mechanisms; both should compose.

## Memory references

- `blackwell_vllm_setup.md` — V1 + FlashInfer + cu128 install path
- `memento_overlay_setup.md` — restart_mode + Qwen3 token-ID repurposing
- `memento_overlay_multiturn_cliff.md` — the cascade finding + last-only fix

## Files of interest

```
studies/lifetime_cost/paper2/
├── README.md                          stack details
├── FINDINGS.md                        microbench scaling result
├── FINDINGS_swebench.md               real-task fair comparison
├── HANDOFF.md                         (this file)
├── adapters/memento_vllm.py           ChatModel wrapper
├── policy/memento_policy.py           lazy memento policy
├── memento_writer/haiku_writer.py     v0 writer (will be replaced by SFT model)
├── tests/
│   ├── test_transform_messages.py     unit tests
│   ├── smoke_*.py                     GPU smoke tests
│   ├── microbench_masking.py          fixed-prompt microbench
│   └── microbench_growing.py          growing-trajectory microbench
├── v0_demo.py                         longdoc demo
├── v0_swebench.py                     real swebench driver
├── find_easy_tasks.py                 sample tractable instances
└── out_v0_swebench/                   saved trajectories
```

External: `external/memento/` is a clone of microsoft/memento for the
overlay. Untracked (gitignored). Re-fetch with:
```
git clone --depth 1 https://github.com/microsoft/memento external/memento
```
