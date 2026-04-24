"""Modal batch runner for AdaptiveCache SWE-bench experiments.

Runs SWE-bench instances on Qwen2.5-7B via the deployed serve_v2.py web endpoint.
All policies (none, fifo, adaptive, kv_adaptive) go through the same vLLM server
so comparisons are fair.

Setup (one-time):
    modal deploy modal_app/serve_v2.py        # get the public URL
    # copy the URL from the Modal dashboard, e.g.:
    # https://vlad--adaptivecache-server-v2-llmserver-serve.modal.run

Usage:
    modal run modal_app/run_experiments.py --vllm-url https://<url>
    modal run modal_app/run_experiments.py --vllm-url https://<url> --policy adaptive --n-instances 5
    modal run modal_app/run_experiments.py --vllm-url https://<url> --policy kv_adaptive --budget 32768

Policies:
    none         — full context, no eviction (baseline)
    fifo         — oldest-first message eviction
    adaptive     — position-aware message eviction (maximises prefix cache)
    kv_adaptive  — adaptive + KV-block eviction via LMCache (Tier 1+2)
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import modal

# ---------------------------------------------------------------------------
# Modal infrastructure
# ---------------------------------------------------------------------------

# Lightweight CPU image for running the harness + adaptive-cache package.
# vLLM/LMCache are NOT installed here — inference goes through LMCacheClient
# which calls the separate LLMServer (modal_app/serve.py).
experiment_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git")
    .pip_install(
        "tiktoken>=0.7",
        "numpy>=1.24",
        "datasets>=2.0",
        "mini-swe-agent>=2.0",
        "litellm>=1.0",
        "modal>=1.3",
        "httpx>=0.24",
        "openai>=1.0",
    )
    .env({"PYTHONPATH": "/app/src"})
    # add_local_* must come last (Modal requirement); copy=True bakes files into image
    .add_local_dir(
        "/Users/cnmsr/Projects/cacheKarpathy/src",
        remote_path="/app/src",
        copy=True,
    )
    .add_local_file(
        "/Users/cnmsr/Projects/cacheKarpathy/modal_app/swebench_instances.json",
        remote_path="/app/swebench_instances.json",
        copy=True,
    )
)

app = modal.App("adaptivecache-experiments")

results_volume = modal.Volume.from_name(
    "adaptivecache-results", create_if_missing=True
)

# SWE-bench Lite default instances (first 5 for quick smoke tests)
DEFAULT_INSTANCES = [
    "astropy__astropy-12907",
    "django__django-11039",
    "django__django-11049",
    "django__django-11099",
    "django__django-11133",
]


# ---------------------------------------------------------------------------
# Per-instance experiment function
# ---------------------------------------------------------------------------


@app.function(
    image=experiment_image,
    volumes={"/results": results_volume},
    timeout=7200,   # 2 hours — astropy instances need 40-90 steps at ~30s/step
    cpu=2,
)
def run_swebench_instance(
    instance_id: str,
    policy: str = "adaptive",
    budget: int = 32768,
    vllm_url: str = "",   # public URL of deployed serve_v2.py, e.g. https://...modal.run
    max_steps: int = 100,
) -> dict:
    """Run one SWE-bench instance with a specific cache policy.

    Args:
        instance_id: SWE-bench instance ID (e.g. "django__django-11039").
        policy: Cache policy name.
        budget: Soft token budget.
        model_name: Model to use for inference.
        max_steps: Maximum agent steps.

    Returns:
        dict with keys: instance_id, policy, resolved, steps, cache_stats, error.
    """
    import traceback

    qwen_model = "openai/Qwen/Qwen3-30B-A3B"

    result: dict = {
        "instance_id": instance_id,
        "policy": policy,
        "budget": budget,
        "model_name": qwen_model,
        "resolved": False,
        "steps": 0,
        "cache_stats": {},
        "error": None,
        "timestamp": time.time(),
    }

    try:
        from harness.swe_config import SWEConfig
        from harness.swe_runner import run_experiment

        output_dir = Path("/results") / f"{policy}_{budget}" / instance_id

        # Point litellm at the deployed unified server's OpenAI endpoint.
        if vllm_url:
            import os
            os.environ["OPENAI_API_BASE"] = f"{vllm_url.rstrip('/')}/v1"
            os.environ["OPENAI_API_KEY"] = "dummy"

        # For kv_adaptive: wire LMCacheClient to the unified server so
        # delete_kv_blocks() can call the LMCache internal API via Modal.
        if policy == "kv_adaptive":
            import sys
            sys.path.insert(0, "/app/src")
            try:
                from harness.lmcache_client import LMCacheClient
                import modal as _modal
                _LLMServer = _modal.Cls.from_name("adaptivecache-unified", "LLMServer")
                # Monkey-patch the client onto AdaptiveCacheModel at import time
                os.environ["ADAPTIVECACHE_KV_SERVER_APP"] = "adaptivecache-unified"
            except Exception:
                pass

        config = SWEConfig(
            dataset="lite",
            split="test",
            instance_ids=[instance_id],
            model_name=qwen_model,
            cache_policy=policy,
            cache_budget=budget,
            max_steps=max_steps,
            output_dir=str(output_dir.parent),
            run_id=instance_id,
        )

        run_dir = run_experiment(config)

        # Collect results
        pred_file = run_dir / instance_id / "predictions.json"
        if pred_file.exists():
            preds = json.loads(pred_file.read_text())
            result["resolved"] = preds.get("resolved", False)
            result["steps"] = preds.get("num_steps", 0)

        # Collect cache trace
        trace_files = list(run_dir.glob("**/*.traj.json"))
        if trace_files:
            traj = json.loads(trace_files[0].read_text())
            cache_trace = traj.get("cache_trace", [])
            if cache_trace:
                # Summarize cache stats
                total_prompt = sum(e.get("prompt_tokens", 0) for e in cache_trace)
                total_cached = sum(
                    e.get("num_cached_tokens", 0)
                    or e.get("cache_read_tokens", 0)
                    for e in cache_trace
                )
                result["cache_stats"] = {
                    "total_steps": len(cache_trace),
                    "total_prompt_tokens": total_prompt,
                    "total_cached_tokens": total_cached,
                    "mean_hit_rate": total_cached / max(total_prompt, 1),
                    "cache_trace": cache_trace,
                }

        # Persist result JSON to volume
        result_file = output_dir / "result.json"
        result_file.parent.mkdir(parents=True, exist_ok=True)
        result_file.write_text(json.dumps(result, indent=2))
        results_volume.commit()

    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"

    return result


# ---------------------------------------------------------------------------
# Local entrypoint: run experiment matrix
# ---------------------------------------------------------------------------


@app.local_entrypoint()
def main(
    vllm_url: str = "",
    policy: str = "adaptive",
    budget: int = 32768,
    n_instances: int = 5,
    max_steps: int = 100,
    instance_ids: str = "",
):
    """Run experiment matrix locally, dispatching to Modal workers.

    Args:
        policy: Cache policy to evaluate.
        budget: Soft token budget.
        n_instances: Number of SWE-bench instances to run.
        model_name: Model name for inference.
        max_steps: Maximum steps per instance.
        instance_ids: Comma-separated instance IDs (overrides n_instances if provided).
    """
    # Determine which instances to run
    if instance_ids:
        instances = [i.strip() for i in instance_ids.split(",") if i.strip()]
    else:
        instances = DEFAULT_INSTANCES[:n_instances]

    if not vllm_url:
        print("WARNING: --vllm-url not set. Deploy serve_v2.py first:")
        print("  modal deploy modal_app/serve_v2.py")
        print("  Then copy the web endpoint URL from the Modal dashboard.")
        print()

    print(f"AdaptiveCache SWE-bench experiment")
    print(f"  Policy: {policy}  Budget: {budget}  Model: Qwen/Qwen2.5-7B-Instruct")
    print(f"  vLLM URL: {vllm_url or '(not set — will fail)'}")
    print(f"  Instances ({len(instances)}): {instances}")
    print()

    start = time.time()

    # Dispatch all instances in parallel via Modal
    results = list(
        run_swebench_instance.map(
            instances,
            kwargs={
                "policy": policy,
                "budget": budget,
                "vllm_url": vllm_url,
                "max_steps": max_steps,
            },
        )
    )

    elapsed = time.time() - start

    # Print summary
    resolved = sum(1 for r in results if r.get("resolved"))
    total = len(results)
    errors = [r for r in results if r.get("error")]

    print(f"\n=== Results ({elapsed:.1f}s) ===")
    print(f"Resolved: {resolved}/{total} ({100*resolved/max(total,1):.1f}%)")
    print()

    for r in results:
        status = "PASS" if r.get("resolved") else "FAIL"
        err = f"  ERROR: {r['error'][:80]}" if r.get("error") else ""
        cs = r.get("cache_stats", {})
        hit_rate = cs.get("mean_hit_rate", 0.0)
        print(
            f"  [{status}] {r['instance_id']}  "
            f"steps={r.get('steps', '?')}  "
            f"hit_rate={hit_rate:.1%}{err}"
        )

    if errors:
        print(f"\n{len(errors)} instance(s) had errors.")

    # Save aggregate results locally
    summary = {
        "policy": policy,
        "budget": budget,
        "model_name": "Qwen/Qwen2.5-7B-Instruct",
        "instances": instances,
        "resolved": resolved,
        "total": total,
        "resolve_rate": resolved / max(total, 1),
        "elapsed_seconds": elapsed,
        "results": results,
        "timestamp": time.time(),
    }

    out_path = Path("results") / f"experiment_{policy}_{budget}_{int(time.time())}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2))
    print(f"\nSummary saved to: {out_path}")
