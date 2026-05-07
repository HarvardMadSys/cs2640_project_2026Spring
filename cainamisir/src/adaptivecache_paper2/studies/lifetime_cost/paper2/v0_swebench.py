"""v0 demo on SWE-bench Lite live mode — confirm masking fires on a real
agent loop and dump the conversation so we can sanity-check it.

Runs ONE swebench_live task with Qwen3-30B-A3B + MementoVLLMModel +
MementoPolicy. Saves the trajectory to JSONL and pretty-prints each
step's messages so we can see exactly which tool obs got mementos.

Run:
    cd /home/vlad/adaptivecache-paper2
    set -a && . /home/vlad/adaptivecache/.env && set +a
    .venv-paper2/bin/python -m studies.lifetime_cost.paper2.v0_swebench
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_here = Path(__file__).resolve().parent
_repo_root = _here.parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

os.environ.setdefault("VLLM_ATTENTION_BACKEND", "FLASHINFER")
os.environ.setdefault("HF_HOME", "/scratch/hf/")
os.environ.setdefault("VLLM_LOGGING_LEVEL", "WARNING")

from studies.lifetime_cost.pipeline.benchmarks.swebench_live import SWEBenchLive
from studies.lifetime_cost.pipeline.policies.base import NoCompaction
from studies.lifetime_cost.pipeline.runner import run_task

from studies.lifetime_cost.paper2.adapters.memento_vllm import MementoVLLMModel
from studies.lifetime_cost.paper2.policy.memento_policy import MementoPolicy


MODEL = os.environ.get("PAPER2_MODEL", "Qwen/Qwen3-30B-A3B-Instruct-2507")
DEFAULT_INSTANCES = "psf__requests-3362,pallets__flask-5063,pylint-dev__pylint-7080,pytest-dev__pytest-7490"
INSTANCE_IDS = [s.strip() for s in os.environ.get("PAPER2_INSTANCES", DEFAULT_INSTANCES).split(",") if s.strip()]
MIN_OBS_CHARS = int(os.environ.get("PAPER2_MIN_OBS_CHARS", "300"))
MAX_STEPS = int(os.environ.get("PAPER2_MAX_STEPS", "20"))
GPU_MEM_UTIL = float(os.environ.get("PAPER2_GPU_UTIL", "0.92"))
MAX_MODEL_LEN = int(os.environ.get("PAPER2_MAX_LEN", "65000"))
N_SEEDS = int(os.environ.get("PAPER2_N_SEEDS", "1"))
TEMPERATURE = float(os.environ.get("PAPER2_TEMPERATURE", "0.0"))
RECALL_ENABLED = os.environ.get("PAPER2_RECALL", "1") not in ("0", "false", "False")
RECALL_LOW_WATER = float(os.environ.get("PAPER2_RECALL_LOW_WATER", "0.60"))
RECALL_COOLDOWN = int(os.environ.get("PAPER2_RECALL_COOLDOWN", "3"))
OUT_DIR = Path(os.environ.get("PAPER2_OUT_DIR", "/home/vlad/adaptivecache-paper2/studies/lifetime_cost/paper2/out_v0_swebench"))


def _summarize_step(step, idx, max_msg_chars=400):
    """Pretty-print a step's messages_in + response + compaction."""
    print(f"\n  --- step {idx} ---")
    print(f"  wall_ms: {step.wallclock_ms}, prompt_tok: {step.usage.prompt_tokens}, cached: {step.usage.cached_tokens}, completion: {step.usage.completion_tokens}")
    if step.compaction_after is not None:
        ce = step.compaction_after
        print(f"  COMPACTION: in_uncached={ce.compaction_input_uncached_tokens}, out={ce.compaction_output_tokens}, wall_ms={ce.wallclock_ms}")
    print(f"  response: {(step.response.content or '')[:200]!r}")
    if step.response.tool_calls:
        for tc in step.response.tool_calls:
            fn = tc.get("function", {})
            print(f"    tool_call: {fn.get('name')!r}({fn.get('arguments')!r})")


def _run_one_task(task, *, masking: bool, label: str, seed: int = 0, temperature: float = 0.0):
    print(f"\n--- {label} on {task.id} (seed={seed}, T={temperature}) ---")
    model = MementoVLLMModel(
        model_name=MODEL,
        gpu_memory_utilization=GPU_MEM_UTIL,
        max_model_len=MAX_MODEL_LEN,
        masking_enabled=masking,
        debug_masking=False,
        temperature=temperature,
    )
    if masking:
        policy = MementoPolicy(
            min_obs_chars=MIN_OBS_CHARS,
            recall_enabled=RECALL_ENABLED,
            recall_low_water_ratio=RECALL_LOW_WATER,
            recall_cooldown_steps=RECALL_COOLDOWN,
        )
    else:
        policy = NoCompaction()
    t0 = time.perf_counter()
    traj = run_task(
        task, model, policy,
        benchmark_name="swebench_live",
        budget_tokens=24_000,
        hard_budget_tokens=30_000,
        max_completion_tokens=1024,
    )
    wall_total_ms = int((time.perf_counter() - t0) * 1000)

    suffix = f"_seed{seed}" if seed else ""
    out_path = OUT_DIR / f"{label}_{task.id.replace('/', '_')}{suffix}.json"
    with open(out_path, "w") as f:
        json.dump(traj.to_dict(), f, indent=2, default=str)

    chat_wall = sum(s.wallclock_ms for s in traj.steps)
    haiku_wall = sum((s.compaction_after.wallclock_ms if s.compaction_after else 0) for s in traj.steps)
    n_recalls = sum(1 for s in traj.steps if s.recall_before is not None)

    # Read-call distribution: which files did the agent re-read, how often?
    from collections import Counter
    read_paths: Counter = Counter()
    for s in traj.steps:
        for tc in (s.response.tool_calls or []):
            fn = tc.get("function", {}) or {}
            if fn.get("name") == "read_file":
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        import json as _j
                        args = _j.loads(args)
                    except Exception:
                        args = {}
                p = args.get("path", "?") if isinstance(args, dict) else "?"
                read_paths[p] += 1
    top_reads = read_paths.most_common(3)

    print(f"  steps={len(traj.steps)} resolved={traj.resolved} chat_wall={chat_wall}ms haiku_wall={haiku_wall}ms total={wall_total_ms}ms compactions={traj.num_compactions} recalls={n_recalls} final_prompt={traj.steps[-1].usage.prompt_tokens if traj.steps else 0}")
    if top_reads:
        print(f"  top reads: {', '.join(f'{n}× {p}' for p, n in top_reads)}")
    return {
        "task_id": task.id,
        "label": label,
        "seed": seed,
        "temperature": temperature,
        "steps": len(traj.steps),
        "resolved": traj.resolved,
        "chat_wall_ms": chat_wall,
        "haiku_wall_ms": haiku_wall,
        "total_wall_ms": wall_total_ms,
        "num_compactions": traj.num_compactions,
        "num_recalls": n_recalls,
        "final_prompt_tokens": traj.steps[-1].usage.prompt_tokens if traj.steps else 0,
        "final_answer_truncated": (traj.final_answer or "")[:120],
        "read_paths": dict(read_paths),
    }


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    bench = SWEBenchLive(
        instance_ids=INSTANCE_IDS,
        cache_dir="/scratch/swebench_repos",
        max_steps_per_task=MAX_STEPS,
    )
    tasks = list(bench.tasks())
    print(f"Loaded {len(tasks)} task(s): {[t.id for t in tasks]}")

    # Run order: all baseline first (engine cache reuses across tasks),
    # then all memento (cache reuses again). Avoids re-init thrash.
    masking_default = os.environ.get("PAPER2_MASKING", "both")
    do_baseline = masking_default in ("0", "both")
    do_memento = masking_default in ("1", "both")

    rows = []
    if do_baseline:
        for seed in range(N_SEEDS):
            for task in tasks:
                rows.append(_run_one_task(task, masking=False, label="baseline", seed=seed, temperature=TEMPERATURE))
    if do_memento:
        for seed in range(N_SEEDS):
            for task in tasks:
                rows.append(_run_one_task(task, masking=True, label="memento", seed=seed, temperature=TEMPERATURE))

    # Save aggregate + print pivot
    summary_path = OUT_DIR / "summary.json"
    with open(summary_path, "w") as f:
        json.dump(rows, f, indent=2, default=str)
    print(f"\nSaved aggregate: {summary_path}")

    # Per-variant totals
    print("\n=== per-task ===")
    print(f"{'task_id':<28} {'variant':<10} {'steps':>5} {'resolved':>9} {'chat_ms':>8} {'haiku_ms':>9} {'final_tok':>10} {'compac':>7}")
    for r in rows:
        print(f"{r['task_id']:<28} {r['label']:<10} {r['steps']:>5} {str(r['resolved']):>9} {r['chat_wall_ms']:>8} {r['haiku_wall_ms']:>9} {r['final_prompt_tokens']:>10} {r['num_compactions']:>7}")

    print("\n=== aggregate ===")
    for label in ("baseline", "memento"):
        sel = [r for r in rows if r["label"] == label]
        if not sel:
            continue
        n = len(sel)
        resolved = sum(1 for r in sel if r["resolved"])
        chat_total = sum(r["chat_wall_ms"] for r in sel)
        haiku_total = sum(r["haiku_wall_ms"] for r in sel)
        print(f"  {label}: tasks={n}, resolved={resolved}/{n}, total_chat_wall={chat_total/1000:.1f}s, total_haiku_wall={haiku_total/1000:.1f}s")


if __name__ == "__main__":
    main()
