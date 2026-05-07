# AdaptiveCache τ-bench Phase B — A6000 Handoff

**Audience:** A new Claude Code session running on Vlad's A6000 machine.
**Goal:** run the Phase B τ-bench airline smoke that's currently blocked by
cluster queue contention on Harvard FAS RC. Report results back so the
other agent (this transcript's author) can fold them into the Paper 1
matrix.

## Project context (read before starting)

AdaptiveCache (Vlad Cainamisir, Harvard) studies online context management
for LLM agents under a prefix-cache cost model. Paper 1 = importance-scored
compaction; this smoke is part of Phase B (validating that compaction
policies actually differentiate on a real multi-turn agent benchmark).

We already validated on a synthetic longdoc benchmark (Phase A, 3 policies
× 2 tasks, all `resolved=True`, 12 min on an A100-80GB) but found that
benchmark too easy — agent solved in 4 turns and never triggered
compaction, so all policies tied. τ-bench's airline domain has multi-turn
customer-service dialogues that naturally grow to 15K+ tokens, so
compaction must fire on it.

**Read `CLAUDE.md` and `paper/PAPER1_PLAN.md` for the full project frame.**

## Hardware sanity check

The A6000 here has **90 GB VRAM** (NVL-style or pro variant). Plan:
- Use `Qwen/Qwen3-30B-A3B-Instruct-2507` at bf16 (~60 GB weights → ~30 GB
  headroom for KV cache + activations). Comfortable.
- This is the **exact same model** as the cluster Phase A run, so τ-bench
  numbers from this machine are apples-to-apples comparable to the
  longdoc baselines on the cluster.

Confirm before running: `nvidia-smi` should show `≥80 GB` of free VRAM.
If for some reason the GPU is shared and only ~40 GB is actually
available, fall back to `Qwen/Qwen3-14B-Instruct-2507` (~28 GB) and
update the model name in the YAML config.

## Setup

```bash
# 1. Clone the repo (or pull if already present)
git clone <wherever-vlad-pushes-to> adaptivecache && cd adaptivecache
# (Or rsync from the cluster: rsync -av holyrc:adaptivecache/ ~/adaptivecache/)

# 2. Python env. Any 3.10+ env with these deps works:
pip install "transformers>=4.50" "torch>=2.4" "huggingface_hub>=0.36" \
            "tiktoken>=0.7" "pyyaml" "matplotlib" "pytest" "apscheduler"
pip install "git+https://github.com/sierra-research/tau-bench"

# 3. Anthropic key (used by τ-bench user simulator)
echo 'ANTHROPIC_API_KEY=<paste the key Vlad shares>' > .env
chmod 600 .env

# 4. Pre-download the model
export HF_HOME=$HOME/hf_cache   # or wherever you have disk
python -c "from huggingface_hub import snapshot_download; \
    snapshot_download('Qwen/Qwen3-30B-A3B-Instruct-2507', max_workers=8)"

# 5. Sanity check: 17 unit tests should pass on CPU
python -m pytest studies/lifetime_cost/tests/ -x -q
```

## What to run

The Phase B smoke is:

```bash
set -a; source .env; set +a
python -m studies.lifetime_cost.scripts.run_main \
    --config studies/lifetime_cost/configs/phase_b_taubench_a6000.yaml
```

This runs **3 airline tasks × 3 policies × 1 model** = 9 cells. The model
loads once per cell (~30s on A6000 from local disk), each task is
20-40 turns, total wall time should be **30-60 min**. User-simulator cost
ceiling: ~**$0.30** of Anthropic credits.

The matrix:
- Model: `Qwen/Qwen3-30B-A3B-Instruct-2507`
- Policies: `none`, `position_aware`, `prefix_preserving`
- Benchmark: `taubench` airline, 3 tasks, user_sim = `claude-haiku-4-5`
- Budget: 8K soft / 16K hard tokens (deliberately tight to force compaction)

## Success criteria

A successful smoke means:
1. **All 9 cells complete without crashing.** (Some tasks may not be solved
   — that's fine; we want the loop to *run*, not 100% resolve rate.)
2. **`n_compactions_total > 0` for at least the `position_aware` and
   `prefix_preserving` policies on at least one task.** This is the
   critical signal — if nothing compacts, the budget is too loose.
3. **Trajectories serialize** to `studies/lifetime_cost/out/phase_b_taubench_a6000/trajectories/.../*.jsonl`.
4. **Cost analysis runs cleanly** and produces `figures/summary.csv` and
   `figures/pareto.png` without `KeyError`s.

## What to report back

Paste these into the reply:

1. **The summary CSV.** It's at
   `studies/lifetime_cost/out/phase_b_taubench_a6000/figures/summary.csv`.
   Columns we care about: `policy`, `n_resolved`, `n_compactions_total`,
   `mean_steps`, `mean_prompt_tokens`, `mean_cliff_blocks`, and the
   `cost_per_resolved__*` columns.

2. **A glance at one trajectory's tool-call sequence.** Pick `none` task 0
   and dump:
   ```python
   import json
   p = 'studies/lifetime_cost/out/phase_b_taubench_a6000/trajectories/taubench/hf_Qwen_Qwen3-14B-Instruct-2507/none.jsonl'
   d = json.loads(open(p).readlines()[0])
   for s in d['steps']:
       u = s['usage']
       tcs = (s['response'].get('tool_calls') or [])
       print(f"step {s['index']}: prompt={u['prompt_tokens']} cached={u.get('cached_tokens',0)}",
             "tools:", [tc['function']['name'] for tc in tcs])
   ```
   This tells us whether the agent is actually using `respond` to talk to
   the customer (it should — if it's only emitting plain text, the runner
   ends after one turn and the smoke is broken).

3. **Any errors or weird behavior** — model OOM, the agent looping on
   `transfer_to_human_agents`, user simulator returning empty messages,
   tau-bench `Action` validation errors, etc. Quote the traceback or log
   excerpt. This is the high-value signal — gotchas surface here, not in
   the success path.

4. **Wall time** for the full smoke (`time` the python invocation).

## Known potential gotchas

- **`Qwen3_5ForConditionalGeneration` confusion.** If you accidentally try
  to load a Qwen 3.5/3.6 model, our adapter at
  `studies/lifetime_cost/pipeline/models/hf_local.py` auto-detects via
  `vision_config` and switches to `AutoModelForImageTextToText`. For Qwen3
  (no .5/.6) this code path doesn't fire — should just be plain
  `AutoModelForCausalLM`.

- **Hermes tool-call regex.** Our adapter parses
  `<tool_call>{...}</tool_call>` from the assistant text. Qwen3's chat
  template emits exactly this format when `tools=` is passed. If for some
  reason the model emits a JSON object directly (no wrapper), the runner
  will see no tool calls and end the conversation early. Symptom: 1-step
  trajectories. If this happens, paste the raw `resp.content` from one
  turn so we can adjust the regex.

- **litellm noise.** You'll see harmless `apscheduler` and
  `cold_storage_handler` import errors flooding stderr from `litellm`.
  Ignore them — installing `apscheduler` (in the setup above) silences
  the loop, but a few lines may still leak through.

- **τ-bench user simulator costs.** Each task is 20-40 turns × ~3K input
  tokens to Haiku ≈ $0.03 / task; budget cap of $0.30 for the whole
  smoke. If you see Haiku rate-limit errors, the run will retry — that's
  fine; if it fails out, switch `user_model` in the YAML to
  `claude-haiku-4-5-20251001` (the dated alias) or report back.

- **`_compactions_total = 0` everywhere.** That means the budget didn't
  bite. Tighten in `phase_b_taubench_a6000.yaml`: drop `budget_tokens` to
  4000.

## File map (everything you need)

```
studies/lifetime_cost/
├── A6000_HANDOFF.md                              ← THIS FILE
├── configs/
│   └── phase_b_taubench_a6000.yaml               ← config to run
├── pricing.yaml                                  ← already has 14B entry
├── pipeline/
│   ├── models/hf_local.py                        ← HFLocalModel adapter
│   ├── benchmarks/taubench.py                    ← τ-bench wrapper
│   ├── policies/{none, position_aware, prefix_preserving, ...}.py
│   ├── runner.py                                 ← agent loop
│   ├── harness.py                                ← matrix orchestrator
│   └── analysis.py                               ← plots + summary CSV
├── scripts/
│   └── run_main.py                               ← entry point
└── tests/                                        ← 17 unit tests, must pass
```

## Reply template (paste this back to Vlad)

```
Phase B A6000 smoke — done in <X> minutes.

Summary (n_resolved / n_compactions_total / mean_cliff_blocks per policy):
  none:               <r>/3, <c> compactions, cliff=<x>
  position_aware:     <r>/3, <c> compactions, cliff=<x>
  prefix_preserving:  <r>/3, <c> compactions, cliff=<x>

Cost per resolved (under qwen/qwen3-30b-a3b column): <values>

[paste full summary.csv here]

[paste trajectory step-dump for none/task-0 here]

Gotchas: <or "none">

Wall time: <minutes>
```
