"""Top-level run-many-things orchestrator.

Given a config (model x policy x benchmark x seeds), iterate over the
cross-product, run each cell, and persist trajectories to disk.

The result of one run is one JSONL file per (model, policy, benchmark)
cell, with one trajectory per line. This shape is what analysis.py
consumes.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List

from .benchmarks import build_benchmark
from .models import build_model
from .policies import build_policy
from .runner import run_task
from .types import Trajectory


def _safe(name: str) -> str:
    return name.replace("/", "_").replace(":", "_")


def run_cell(
    *,
    model_name: str,
    policy_name: str,
    benchmark_name: str,
    benchmark_kwargs: Dict[str, Any] | None = None,
    policy_kwargs: Dict[str, Any] | None = None,
    model_kwargs: Dict[str, Any] | None = None,
    budget_tokens: int = 32_000,
    hard_budget_tokens: int = 64_000,
    out_dir: str = "studies/lifetime_cost/out",
    n_workers: int = 1,
    max_tasks: int | None = None,
) -> Path:
    """Run all tasks for one (model, policy, benchmark) cell."""
    out = Path(out_dir) / "trajectories" / _safe(benchmark_name) / _safe(model_name)
    out.mkdir(parents=True, exist_ok=True)
    out_path = out / f"{_safe(policy_name)}.jsonl"

    benchmark = build_benchmark(benchmark_name, **(benchmark_kwargs or {}))
    tasks = list(benchmark.tasks())
    if max_tasks is not None:
        tasks = tasks[:max_tasks]

    # Each task gets its own model + policy instance (policies are stateful)
    def run_one(task):
        m = build_model(model_name, **(model_kwargs or {}))
        p = build_policy(policy_name, **(policy_kwargs or {}))
        return run_task(
            task, m, p,
            benchmark_name=benchmark_name,
            budget_tokens=budget_tokens,
            hard_budget_tokens=hard_budget_tokens,
        )

    written = 0
    with open(out_path, "w") as f:
        if n_workers <= 1:
            for task in tasks:
                traj = run_one(task)
                f.write(json.dumps(traj.to_dict()) + "\n")
                f.flush()
                written += 1
                print(f"[{policy_name}/{model_name}/{benchmark_name}] {written}/{len(tasks)} {task.id} resolved={traj.resolved}")
        else:
            with ThreadPoolExecutor(max_workers=n_workers) as ex:
                futs = {ex.submit(run_one, t): t for t in tasks}
                for fut in as_completed(futs):
                    traj = fut.result()
                    f.write(json.dumps(traj.to_dict()) + "\n")
                    f.flush()
                    written += 1
                    print(f"[{policy_name}/{model_name}/{benchmark_name}] {written}/{len(tasks)} {futs[fut].id} resolved={traj.resolved}")

    return out_path


def run_matrix(config: Dict[str, Any]) -> List[Path]:
    """Run a model × policy × benchmark matrix from a config dict.

    Config schema (see configs/main.yaml):
      models:     [model_name, ...]
      policies:   [{name: ..., kwargs: ...}, ...]
      benchmarks: [{name: ..., kwargs: ...}, ...]
      budget_tokens: int
      hard_budget_tokens: int
      max_tasks: int | null
      n_workers: int
      out_dir: str
    """
    out_paths = []
    for benchmark_spec in config["benchmarks"]:
        for model_name in config["models"]:
            for policy_spec in config["policies"]:
                p = run_cell(
                    model_name=model_name,
                    policy_name=policy_spec["name"],
                    policy_kwargs=policy_spec.get("kwargs", {}),
                    benchmark_name=benchmark_spec["name"],
                    benchmark_kwargs=benchmark_spec.get("kwargs", {}),
                    model_kwargs=config.get("model_kwargs", {}).get(model_name, {}),
                    budget_tokens=config.get("budget_tokens", 32_000),
                    hard_budget_tokens=config.get("hard_budget_tokens", 64_000),
                    out_dir=config.get("out_dir", "studies/lifetime_cost/out"),
                    n_workers=config.get("n_workers", 1),
                    max_tasks=config.get("max_tasks"),
                )
                out_paths.append(p)
    return out_paths


def load_trajectories(out_dir: str | Path) -> List[Trajectory]:
    """Reverse of run_matrix: load all .jsonl trajectories under out_dir."""
    from .types import Message, Step, Usage, CompactionEvent

    out_dir = Path(out_dir)
    trajs: List[Trajectory] = []
    for path in sorted(out_dir.rglob("*.jsonl")):
        with open(path) as f:
            for line in f:
                if not line.strip():
                    continue
                d = json.loads(line)
                steps = []
                for s in d.get("steps", []):
                    msgs = [Message(**m) for m in s.get("messages_in", [])]
                    resp_d = s.get("response", {})
                    response = Message(**{
                        "role": resp_d.get("role", "assistant"),
                        "content": resp_d.get("content", ""),
                        "tool_calls": resp_d.get("tool_calls"),
                        "tool_call_id": resp_d.get("tool_call_id"),
                        "name": resp_d.get("name"),
                    })
                    usage = Usage(**s.get("usage", {"prompt_tokens": 0, "completion_tokens": 0}))
                    comp = s.get("compaction_after")
                    comp_evt = CompactionEvent(**comp) if comp else None
                    steps.append(Step(
                        index=s["index"],
                        messages_in=msgs,
                        response=response,
                        usage=usage,
                        wallclock_ms=s.get("wallclock_ms", 0),
                        compaction_after=comp_evt,
                    ))
                trajs.append(Trajectory(
                    task_id=d["task_id"],
                    benchmark=d["benchmark"],
                    model=d["model"],
                    policy=d["policy"],
                    steps=steps,
                    resolved=d.get("resolved"),
                    final_answer=d.get("final_answer"),
                    extra=d.get("extra", {}),
                ))
    return trajs
