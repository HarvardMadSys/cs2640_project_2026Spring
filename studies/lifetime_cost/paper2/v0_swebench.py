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
INSTANCE_ID = os.environ.get("PAPER2_INSTANCE", "psf__requests-3362")
MIN_OBS_CHARS = int(os.environ.get("PAPER2_MIN_OBS_CHARS", "300"))
MAX_STEPS = int(os.environ.get("PAPER2_MAX_STEPS", "20"))
GPU_MEM_UTIL = float(os.environ.get("PAPER2_GPU_UTIL", "0.85"))
MAX_MODEL_LEN = int(os.environ.get("PAPER2_MAX_LEN", "32000"))
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


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    bench = SWEBenchLive(
        instance_ids=[INSTANCE_ID],
        cache_dir="/scratch/swebench_repos",
        max_steps_per_task=MAX_STEPS,
    )
    tasks = list(bench.tasks())
    if not tasks:
        print(f"ERROR: no tasks for instance_id={INSTANCE_ID}")
        return
    task = tasks[0]
    print(f"Task: {task.id}, max_steps={task.max_steps}")

    masking = os.environ.get("PAPER2_MASKING", "1") == "1"
    label = "memento" if masking else "baseline"

    print(f"\n--- starting {label} run on {task.id} ---")
    model = MementoVLLMModel(
        model_name=MODEL,
        gpu_memory_utilization=GPU_MEM_UTIL,
        max_model_len=MAX_MODEL_LEN,
        masking_enabled=masking,
        debug_masking=masking,  # log BlockMasking events for masking variant
    )
    policy = MementoPolicy(min_obs_chars=MIN_OBS_CHARS) if masking else NoCompaction()

    t0 = time.perf_counter()
    traj = run_task(
        task, model, policy,
        benchmark_name="swebench_live",
        budget_tokens=24_000,
        hard_budget_tokens=30_000,
        max_completion_tokens=1024,
    )
    wall_total = (time.perf_counter() - t0) * 1000

    # Persist
    out_path = OUT_DIR / f"{label}_{task.id.replace('/', '_')}.json"
    with open(out_path, "w") as f:
        json.dump(traj.to_dict(), f, indent=2, default=str)
    print(f"\nSaved trajectory: {out_path}")

    # Print summary
    print(f"\n=== {label} summary ===")
    print(f"  steps: {len(traj.steps)}")
    print(f"  resolved: {traj.resolved}")
    print(f"  total wall ms: {wall_total:.0f}")
    print(f"  prompt tokens: {traj.total_prompt_tokens}, cached: {traj.total_cached_tokens}, completion: {traj.total_completion_tokens}")
    print(f"  num compactions: {traj.num_compactions}")
    print(f"  final answer: {(traj.final_answer or '')[:200]!r}")

    # Per-step view
    print(f"\n=== per-step trace ({len(traj.steps)} steps) ===")
    for i, step in enumerate(traj.steps):
        _summarize_step(step, i)


if __name__ == "__main__":
    main()
