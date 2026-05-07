"""Recall validation across multiple strategies.

Compares recall strategies side-by-side on the same set of SWE-bench Lite
tasks, in one process so the vLLM engine cache is reused across runs.

Strategies (PAPER2_RECALL_VARIANTS, comma-separated). Variant grammar:
`<base>` or `<base>-<mode>`. Base picks WHAT to recall, mode picks HOW
the recalled obs comes back into the prompt.

Bases:
  off            — no recall (control; reproduces vanilla memento behavior)
  lru            — restore the most-recently-evicted memento (content-blind floor)
  embedding      — restore the memento whose text is most similar to the
                   trailing trajectory window (sentence-transformers MiniLM)

Modes:
  inplace (default) — clear `memento` on the original message; renderer
                      drops in the full obs at the original chronological
                      position. Tokens added in the middle shift the suffix
                      → prefix cache misses past that point (cliff cost).
  append            — leave the original message mementoed; push a synthetic
                      user message at the END containing the recalled obs.
                      Prefix preserved → cache hits everything before the
                      addendum (cheap recall). The "stale historical truth"
                      mechanism at the prompt layer.

Examples:
  PAPER2_RECALL_VARIANTS=off,lru,embedding              # v1 (inplace) baseline
  PAPER2_RECALL_VARIANTS=off,lru,lru-append             # A/B inplace vs append
  PAPER2_RECALL_VARIANTS=lru,lru-append,embedding,embedding-append   # full grid

For each task we report read_file counts per file, recall fire counts,
walltime, and whether the run resolved.

    cd /home/vlad/adaptivecache-paper2
    set -a && . /home/vlad/adaptivecache/.env && set +a
    .venv-paper2/bin/python -m studies.lifetime_cost.paper2.validate_recall
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

_here = Path(__file__).resolve().parent
_repo_root = _here.parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

os.environ.setdefault("VLLM_ATTENTION_BACKEND", "FLASHINFER")
os.environ.setdefault("HF_HOME", "/scratch/hf/")
os.environ.setdefault("VLLM_LOGGING_LEVEL", "WARNING")

from studies.lifetime_cost.pipeline.benchmarks.swebench_live import SWEBenchLive
from studies.lifetime_cost.pipeline.runner import run_task
from studies.lifetime_cost.paper2.adapters.memento_vllm import MementoVLLMModel
from studies.lifetime_cost.paper2.policy.memento_policy import MementoPolicy


MODEL = os.environ.get("PAPER2_MODEL", "Qwen/Qwen3-30B-A3B-Instruct-2507")
DEFAULT_INSTANCES = "psf__requests-3362,pytest-dev__pytest-7490,pytest-dev__pytest-5413,pylint-dev__pylint-5859"
INSTANCE_IDS = [s.strip() for s in os.environ.get(
    "PAPER2_INSTANCES",
    os.environ.get("PAPER2_INSTANCE", DEFAULT_INSTANCES)
).split(",") if s.strip()]
MAX_STEPS = int(os.environ.get("PAPER2_MAX_STEPS", "20"))
GPU_MEM_UTIL = float(os.environ.get("PAPER2_GPU_UTIL", "0.92"))
MAX_MODEL_LEN = int(os.environ.get("PAPER2_MAX_LEN", "65000"))
# T=0 makes seeds meaningless — multi-seed averaging requires real
# stochasticity. Default T=0.6 matches the prior v0_swebench protocol.
TEMPERATURE = float(os.environ.get("PAPER2_TEMPERATURE", "0.6"))
N_SEEDS = int(os.environ.get("PAPER2_N_SEEDS", "1"))
SEED_BASE = int(os.environ.get("PAPER2_SEED_BASE", "0"))
# When set, override the variant grammar's automatic seed sweep; useful
# for filling in partial data from a previous bake that timed out.
RECALL_VARIANTS = [s.strip() for s in os.environ.get(
    "PAPER2_RECALL_VARIANTS", "off,lru,embedding"
).split(",") if s.strip()]
EMBEDDING_THRESHOLD = float(os.environ.get("PAPER2_EMBEDDING_THRESHOLD", "0.40"))
RECALL_LOW_WATER = float(os.environ.get("PAPER2_RECALL_LOW_WATER", "0.60"))
RECALL_COOLDOWN = int(os.environ.get("PAPER2_RECALL_COOLDOWN", "3"))
# Trigger budget: 0.85 * BUDGET_TOKENS; smaller value forces compaction to
# fire more often. Knob for Phase 7 / drop-mode smokes where we want many
# compaction events.
BUDGET_TOKENS = int(os.environ.get("PAPER2_BUDGET_TOKENS", "24000"))
HARD_BUDGET_TOKENS = int(os.environ.get("PAPER2_HARD_BUDGET_TOKENS", "30000"))
# Phase 4d: when set to "1"/"true", build the engine with v4 attention-mask
# mode (compactions skip physical KV move; obs blocks are pinned in
# block_pool and filtered from the per-step block_table). All variants in
# the run inherit this engine setting.
ATTENTION_MASK_MODE = os.environ.get(
    "PAPER2_ATTENTION_MASK_MODE", "0"
).lower() in ("1", "true", "yes")
# v8: when False, captured obs KV blocks free naturally on physical
# compaction (under attention_mask_mode=False) instead of being refcount-
# pinned in GPU. The CPU-pinned capture (Phase 1A) is the durable backup;
# recall path can hit vLLM's content-hash prefix cache OR queue_kv_restore
# to copy CPU→GPU. Default True matches v3/v4 behavior.
PIN_CAPTURED_BLOCKS = os.environ.get(
    "PAPER2_PIN_CAPTURED_BLOCKS", "1"
).lower() in ("1", "true", "yes")
OUT_DIR = Path(os.environ.get(
    "PAPER2_OUT_DIR",
    "/home/vlad/adaptivecache-paper2/studies/lifetime_cost/paper2/out_v0_swebench",
))


def _build_policy(variant: str) -> MementoPolicy:
    """Build a policy for a named variant.

    Variant grammar: `<base>` or `<base>-<mode>`. Base is one of
    `off|lru|embedding|model`. Mode is one of `inplace|append|attmask`; if
    omitted it defaults to `inplace` (the v1 behavior). `off` ignores mode.

    The `model` base disables policy-driven recall and exposes a
    `recall(memento_id)` tool the agent can call deliberately. Mode in
    that case must be `attmask` so the recalled obs comes back via
    Phase 4e KV unmask (no re-prefill).
    """
    if "-" in variant:
        base, mode = variant.split("-", 1)
    else:
        base, mode = variant, "inplace"
    if mode not in ("inplace", "append", "attmask", "drop", "kvrestore"):
        raise ValueError(f"unknown recall mode in variant {variant!r}: {mode!r}")

    if base == "none":
        # True full-context baseline: no compaction, no recall.
        return MementoPolicy(
            min_obs_chars=300,
            compaction_enabled=False,
            recall_enabled=False,
        )
    if base == "off":
        return MementoPolicy(min_obs_chars=300, recall_enabled=False)
    if base == "lru":
        # Eager-at-suffix: only meaningful for kvrestore mode, where
        # compacting the just-arrived obs (while still last-tool) keeps
        # the memento appendix at the suffix tail and prevents the
        # mid-prompt insertion cliff. Other modes don't need it.
        eager = (mode == "kvrestore")
        return MementoPolicy(
            min_obs_chars=300,
            recall_enabled=True,
            recall_low_water_ratio=RECALL_LOW_WATER,
            recall_cooldown_steps=RECALL_COOLDOWN,
            recall_strategy="lru",
            recall_mode=mode,
            eager_compact_at_suffix=eager,
        )
    if base == "embedding":
        return MementoPolicy(
            min_obs_chars=300,
            recall_enabled=True,
            recall_low_water_ratio=RECALL_LOW_WATER,
            recall_cooldown_steps=RECALL_COOLDOWN,
            recall_strategy="embedding",
            recall_strategy_kwargs={"threshold": EMBEDDING_THRESHOLD},
            recall_mode=mode,
        )
    if base == "model":
        if mode != "attmask":
            raise ValueError(
                f"variant 'model-{mode}' requires mode='attmask' "
                f"(KV unmask is the only zero-prefill recall path)"
            )
        return MementoPolicy(
            min_obs_chars=300,
            # No policy-driven recall — the model decides via the tool.
            recall_enabled=False,
            recall_mode=mode,
            recall_tool_enabled=True,
        )
    raise ValueError(f"unknown variant: {variant!r}")


def _run(task, model, *, variant: str, seed: int = 0):
    label = f"memento_{variant}"
    print(f"\n--- {label} on {task.id} (T={TEMPERATURE}, seed={seed}) ---")
    policy = _build_policy(variant)

    # Override the model's default_seed for this run
    model._default_seed = seed

    # Flush ALL stale Phase 9 state from prior cells. The engine is reused
    # across cells; without these resets, a stale memento_id from cell N
    # can be drained at the start of cell N+1, and the splice points to
    # logical positions / blocks from cell N (different prompt entirely)
    # → num_computed_tokens bumped past cell N+1's prompt length →
    # `assert total_num_scheduled_tokens > 0` in _prepare_inputs.
    try:
        from vllm.v1.core.block_masking.memento_store import (
            reset_recall_queue,
            reset_captured_obs,
            reset_kv_restore_queue,
            reset_dual_key_queue,
        )
        # IPC resets only — these are filesystem and visible to the engine
        # subprocess. (reset_global_memento_store is process-local and
        # only affects the main proc's singleton; engine still has its
        # own. Mismatched store/queue would cause hash-lookup misses
        # rather than crashes anyway.)
        reset_recall_queue()
        reset_captured_obs()
        reset_kv_restore_queue()
        reset_dual_key_queue()
    except Exception:
        pass

    t0 = time.perf_counter()
    traj = run_task(
        task, model, policy,
        benchmark_name="swebench_live",
        budget_tokens=BUDGET_TOKENS,
        hard_budget_tokens=HARD_BUDGET_TOKENS,
        max_completion_tokens=1024,
    )
    wall_total = int((time.perf_counter() - t0) * 1000)

    suffix = f"_seed{seed}" if N_SEEDS > 1 else ""
    out_path = OUT_DIR / f"{label}_{task.id.replace('/', '_')}{suffix}.json"
    with open(out_path, "w") as f:
        json.dump(traj.to_dict(), f, indent=2, default=str)

    chat_wall = sum(s.wallclock_ms for s in traj.steps)
    haiku_wall = sum(
        (s.compaction_after.wallclock_ms if s.compaction_after else 0)
        for s in traj.steps
    )
    n_recalls = sum(1 for s in traj.steps if s.recall_before is not None)

    reads: Counter = Counter()
    for s in traj.steps:
        for tc in (s.response.tool_calls or []):
            fn = tc.get("function", {}) or {}
            if fn.get("name") == "read_file":
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                p = args.get("path", "?") if isinstance(args, dict) else "?"
                reads[p] += 1

    print(f"  steps={len(traj.steps)} resolved={traj.resolved} "
          f"chat_wall={chat_wall}ms haiku_wall={haiku_wall}ms total={wall_total}ms "
          f"compactions={traj.num_compactions} recalls={n_recalls} "
          f"final_prompt={traj.steps[-1].usage.prompt_tokens if traj.steps else 0}")
    print(f"  read_file calls: {sum(reads.values())} total")
    for p, n in reads.most_common(5):
        print(f"    {n:>3}× {p}")
    return {
        "task_id": task.id,
        "variant": variant,
        "seed": seed,
        "label": label,
        "steps": len(traj.steps),
        "resolved": traj.resolved,
        "chat_wall_ms": chat_wall,
        "haiku_wall_ms": haiku_wall,
        "total_wall_ms": wall_total,
        "num_compactions": traj.num_compactions,
        "num_recalls": n_recalls,
        "reads": dict(reads),
    }


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Phase 4d-diag: tell the engine subprocess where to dump its
    # ENGINE_STATS counters at process exit. Must be set BEFORE the
    # MementoVLLMModel constructor builds the engine (which forks the
    # worker subprocess; the env var propagates).
    engine_stats_path = OUT_DIR / "v4_engine_stats.jsonl"
    os.environ["PAPER2_ENGINE_STATS_PATH"] = str(engine_stats_path)
    print(f"engine_stats_path: {engine_stats_path}")

    bench = SWEBenchLive(
        instance_ids=INSTANCE_IDS,
        cache_dir="/scratch/swebench_repos",
        max_steps_per_task=MAX_STEPS,
    )
    tasks = list(bench.tasks())
    if not tasks:
        raise SystemExit(f"No tasks loaded from {INSTANCE_IDS!r}")

    print(f"variants: {RECALL_VARIANTS}")
    print(f"tasks: {[t.id for t in tasks]}")

    print(f"attention_mask_mode: {ATTENTION_MASK_MODE}")
    # Auto-capture mementos at compaction is needed by:
    #   - attmask mode (Phase 4d): blocks get refcount-pinned via capture_specs
    #   - kvrestore mode (Phase 3c+9): captured CPU KV is what we restore on
    #     recall; without capture there's nothing to restore.
    needs_capture = ATTENTION_MASK_MODE or any(
        "kvrestore" in v for v in RECALL_VARIANTS
    )
    # Phase 9: respect PAPER2_NO_PIN env var (set by Modal entrypoint --no-pin
    # flag) so the adapter doesn't clobber it when constructing the engine.
    # pin_captured_blocks=False → adapter sets PAPER2_NO_PIN=1 → engine
    # skips Phase 4a refcount pin, captured blocks become eviction-eligible.
    pin_captured = os.environ.get("PAPER2_NO_PIN", "0").lower() not in ("1", "true", "yes")
    print(f"pin_captured_blocks: {pin_captured}")
    model = MementoVLLMModel(
        model_name=MODEL,
        gpu_memory_utilization=GPU_MEM_UTIL,
        max_model_len=MAX_MODEL_LEN,
        masking_enabled=True,
        debug_masking=False,
        temperature=TEMPERATURE,
        attention_mask_mode=ATTENTION_MASK_MODE,
        auto_capture_mementos=needs_capture,
        pin_captured_blocks=pin_captured,
        # Phase 4d-diag: under last_only_masking=True the renderer puts
        # markers only on the LAST tool message — but the last tool msg
        # is always fresh (no memento yet), so markers are never in the
        # prompt and the engine never fires compaction. Under
        # attention_mask_mode the per-call compaction is cheap (refcount
        # pin + filter, no physical KV move), so we render markers on
        # every memento'd tool message to actually exercise the v4 path.
        last_only_masking=not (ATTENTION_MASK_MODE or any(
            "kvrestore" in v for v in RECALL_VARIANTS
        )),
    )

    # Run order: seed outermost (so within a seed, all variants see the
    # same RNG starting point); variant in the middle; tasks innermost.
    rows: List[Dict[str, Any]] = []
    for k in range(N_SEEDS):
        seed = SEED_BASE + k
        for variant in RECALL_VARIANTS:
            for t in tasks:
                rows.append(_run(t, model, variant=variant, seed=seed))

    # Tabular summary
    print("\n=== per-run summary ===")
    print(f"  {'task':<32} {'variant':<10} {'seed':>4} {'steps':>5} {'res':>4} "
          f"{'reads':>5} {'top_path':<28} {'top_n':>5} {'recs':>5} {'wall_s':>7}")
    for r in rows:
        top = max(r["reads"].items(), key=lambda kv: kv[1]) if r["reads"] else ("-", 0)
        print(f"  {r['task_id'][:32]:<32} {r['variant']:<10} {r['seed']:>4} "
              f"{r['steps']:>5} {str(r['resolved'])[:4]:>4} {sum(r['reads'].values()):>5} "
              f"{top[0][:28]:<28} {top[1]:>5} {r['num_recalls']:>5} "
              f"{r['total_wall_ms']/1000:>7.1f}")

    # Aggregate across seeds: mean total_reads, top-path reads, resolve rate
    import statistics
    print("\n=== aggregate across seeds (mean ± std) ===")
    print(f"  {'task':<32} {'variant':<10} {'reads':>14} {'top_path':<28} {'top_n':>14} "
          f"{'resolve':>10} {'wall_s':>10}")
    grouped: Dict[tuple, List[Dict[str, Any]]] = {}
    for r in rows:
        grouped.setdefault((r["task_id"], r["variant"]), []).append(r)
    # find top_path per task across all variants/seeds (most consistently re-read file)
    top_paths: Dict[str, str] = {}
    for r in rows:
        if not r["reads"]:
            continue
        cur_top = max(r["reads"].items(), key=lambda kv: kv[1])
        prev = top_paths.get(r["task_id"])
        if prev is None or cur_top[1] > sum(rr["reads"].get(prev, 0) for rr in grouped.get((r["task_id"], r["variant"]), [])):
            # Prefer the path with the highest count in any single run.
            top_paths[r["task_id"]] = cur_top[0]
    for (task_id, variant), runs in grouped.items():
        n = len(runs)
        totals = [sum(rr["reads"].values()) for rr in runs]
        tp = top_paths.get(task_id, "-")
        tp_counts = [rr["reads"].get(tp, 0) for rr in runs]
        resolves = [1 if rr["resolved"] else 0 for rr in runs]
        walls = [rr["total_wall_ms"] / 1000 for rr in runs]
        m_total = statistics.mean(totals)
        s_total = statistics.stdev(totals) if n > 1 else 0
        m_tp = statistics.mean(tp_counts)
        s_tp = statistics.stdev(tp_counts) if n > 1 else 0
        m_res = statistics.mean(resolves)
        m_wall = statistics.mean(walls)
        print(f"  {task_id[:32]:<32} {variant:<10} {m_total:>6.1f}±{s_total:>5.1f} "
              f"{tp[:28]:<28} {m_tp:>6.1f}±{s_tp:>5.1f} "
              f"{m_res:>10.2f} {m_wall:>10.1f}")

    summary_path = OUT_DIR / "validate_recall_summary.json"
    summary_path.write_text(json.dumps(rows, indent=2))
    print(f"\nSaved: {summary_path}")


if __name__ == "__main__":
    main()
