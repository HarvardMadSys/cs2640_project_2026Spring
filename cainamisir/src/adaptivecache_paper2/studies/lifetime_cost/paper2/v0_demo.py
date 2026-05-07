"""v0 demo: longdoc 1-task with Memento masking + Haiku-written mementos.

Run from repo root:
    cd /home/vlad/adaptivecache-paper2
    VLLM_ATTENTION_BACKEND=FLASHINFER \
    HF_HOME=/scratch/hf/ \
    .venv-paper2/bin/python -m studies.lifetime_cost.paper2.v0_demo

Compares two settings on the same synthetic task:
  - baseline: masking disabled, no policy. Vanilla agent loop.
  - memento:  masking enabled, MementoPolicy tags tool obs.

Reports wall-clock per step + total + final answer correctness.
This is a sanity check, not a publication-grade evaluation.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def _setup_path():
    # Allow running this file as a script (python v0_demo.py) by adding the
    # repo root to sys.path. Running via `python -m ...` doesn't need this.
    here = Path(__file__).resolve().parent
    repo_root = here.parents[2]  # studies/lifetime_cost/paper2/.. → repo root
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


_setup_path()

# Required env settings
os.environ.setdefault("VLLM_ATTENTION_BACKEND", "FLASHINFER")
os.environ.setdefault("HF_HOME", "/scratch/hf/")
os.environ.setdefault("VLLM_LOGGING_LEVEL", "WARNING")

from studies.lifetime_cost.pipeline.benchmarks.longdoc import LongDocAgent
from studies.lifetime_cost.pipeline.policies.base import NoCompaction
from studies.lifetime_cost.pipeline.runner import run_task

from studies.lifetime_cost.paper2.adapters.memento_vllm import MementoVLLMModel
from studies.lifetime_cost.paper2.policy.memento_policy import MementoPolicy


MODEL = os.environ.get("PAPER2_MODEL", "Qwen/Qwen3-4B-Instruct-2507")
TARGET_DOC_TOKENS = int(os.environ.get("PAPER2_DOC_TOKENS", "20000"))
N_NEEDLES = int(os.environ.get("PAPER2_NEEDLES", "3"))
MAX_TASKS = 1
MAX_COMPLETION = 512
GPU_MEM_UTIL = float(os.environ.get("PAPER2_GPU_UTIL", "0.4"))
MAX_MODEL_LEN = int(os.environ.get("PAPER2_MAX_LEN", "32000"))


def _summary(label, traj, wall_total_ms, model):
    print(f"\n=== {label} ===")
    print(f"  task: {traj.task_id}")
    print(f"  steps: {len(traj.steps)}")
    print(f"  resolved: {traj.resolved}")
    print(f"  total wall (ms): {wall_total_ms:.0f}")
    print(f"  per-step wall (ms): {[s.wallclock_ms for s in traj.steps]}")
    print(f"  prompt tokens: {traj.total_prompt_tokens}")
    print(f"  cached tokens: {traj.total_cached_tokens}")
    print(f"  completion tokens: {traj.total_completion_tokens}")
    print(f"  num compactions: {traj.num_compactions}")
    print(f"  final: {(traj.final_answer or '')[:200]!r}")


def run_one(label, *, masking: bool, with_policy: bool, task):
    print(f"\n--- starting {label} ---")
    model = MementoVLLMModel(
        model_name=MODEL,
        gpu_memory_utilization=GPU_MEM_UTIL,
        max_model_len=MAX_MODEL_LEN,
        masking_enabled=masking,
        debug_masking=False,
    )
    policy = MementoPolicy(min_obs_chars=1500) if with_policy else NoCompaction()
    t0 = time.perf_counter()
    traj = run_task(
        task,
        model,
        policy,
        benchmark_name="longdoc",
        budget_tokens=20_000,
        hard_budget_tokens=28_000,
        max_completion_tokens=MAX_COMPLETION,
    )
    wall = (time.perf_counter() - t0) * 1000
    _summary(label, traj, wall, model)
    return traj, wall


def main():
    bench = LongDocAgent(
        n_tasks=MAX_TASKS,
        target_doc_tokens=TARGET_DOC_TOKENS,
        n_needles=N_NEEDLES,
        chunk_chars=4_000,
        seed=42,
        corpus="lorem",
    )
    tasks = list(bench.tasks())
    task = tasks[0]
    # Cap steps to keep the demo bounded
    task.max_steps = 8

    print(f"Task: {task.id}, max_steps={task.max_steps}, doc target tokens={TARGET_DOC_TOKENS}")

    # Run the memento variant first; engine is cached per (model_name, ..., masking_enabled)
    # so the second run reuses init when the masking flag matches. We run two variants
    # back-to-back; each cold-loads its own engine.
    if os.environ.get("PAPER2_BASELINE_ONLY"):
        run_one("baseline (no masking)", masking=False, with_policy=False, task=task)
        return
    if os.environ.get("PAPER2_MEMENTO_ONLY"):
        run_one("memento (masking + policy)", masking=True, with_policy=True, task=task)
        return

    # Run both. Memento first — it's the more interesting case to validate.
    run_one("memento (masking + policy)", masking=True, with_policy=True, task=task)
    run_one("baseline (no masking)", masking=False, with_policy=False, task=task)


if __name__ == "__main__":
    main()
