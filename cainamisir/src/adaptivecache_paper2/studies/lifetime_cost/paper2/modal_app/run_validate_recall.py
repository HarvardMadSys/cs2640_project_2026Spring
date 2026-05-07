"""Modal entrypoint: bake validate_recall.py on Modal H100.

Runs the multi-strategy multi-seed bake (off / lru / lru-append / embedding /
embedding-append) on a configurable set of SWE-bench Lite tasks. Trajectories
land in the persistent `paper2-out-v3` volume.

Quick start (after `modal token new` and `modal secret create paper2-anthropic`):

    cd /home/vlad/adaptivecache-paper2
    /home/vlad/adaptivecache/.venv/bin/modal run studies.lifetime_cost.paper2.modal_app.run_validate_recall

Or with overrides:

    /home/vlad/adaptivecache/.venv/bin/modal run studies.lifetime_cost.paper2.modal_app.run_validate_recall \\
        --instances pytest-dev__pytest-7490,pytest-dev__pytest-5413 \\
        --n-seeds 3 \\
        --temperature 0.6 \\
        --variants off,lru-append,embedding-append

After the run, fetch results to local:

    /home/vlad/adaptivecache/.venv/bin/modal volume get paper2-out-v3 / ./modal_out_v3
"""
from __future__ import annotations

import time
import modal

from .image import image, standard_volumes


app = modal.App("paper2-validate-recall")


# Look up the local .env so we can ship ANTHROPIC_API_KEY to Modal as a Secret
# without requiring `modal secret create` separately.
try:
    secret = modal.Secret.from_dotenv("/home/vlad/adaptivecache/.env")
except Exception:
    # Fallback: assume a pre-created secret
    secret = modal.Secret.from_name("paper2-anthropic", required_keys=["ANTHROPIC_API_KEY"])


@app.function(
    gpu="H100",
    image=image,
    volumes=standard_volumes(),
    timeout=60 * 180,  # 3 hour cap (recall-heavy bakes can be slow)
    secrets=[secret],
)
def run_validate_recall(
    instances: str = "pytest-dev__pytest-7490",
    n_seeds: int = 3,
    temperature: float = 0.6,
    variants: str = "off,lru,lru-append,embedding,embedding-append",
    max_steps: int = 20,
    gpu_util: float = 0.92,
    max_model_len: int = 65000,
    attention_mask_mode: bool = False,
    budget_tokens: int = 24_000,
    hard_budget_tokens: int = 30_000,
    recall_low_water: float = 0.60,
    no_pin: bool = False,
    profile_mem: bool = False,
) -> dict:
    """Run validate_recall.py inside the Modal container, return summary dict."""
    import json
    import os
    import sys
    from pathlib import Path

    # Source layout inside the container — see image.py for build steps.
    REPO = "/opt/adaptivecache"
    sys.path.insert(0, REPO)
    os.chdir(REPO)

    # Phase 8: pin PYTHONHASHSEED so adapter (main proc) and engine
    # (subprocess) compute identical NONE_HASH and chain block hashes.
    # vLLM's init_none_hash falls back to os.urandom(32) when this is
    # unset → mismatched NONE_HASH across procs → dual-key inserts
    # would never hit. This MUST be set BEFORE any vLLM import.
    os.environ["PYTHONHASHSEED"] = "1"
    # Phase 9 heartbeat: KNOWN to break --no-pin warmup (race between
    # heartbeat-thread sampling block_pool and main-thread warmup pass).
    # Off until that race is fixed; not gated on --profile-mem so we can
    # still write per-event GPU mem traces safely.
    os.environ.pop("PAPER2_HEARTBEAT_SEC", None)

    # Per-run output dir on the persistent volume.
    run_id = int(time.time())
    out_dir = Path(f"/scratch/out/validate_recall/{run_id}")
    out_dir.mkdir(parents=True, exist_ok=True)
    # Phase 9: GPU memory trace at every kv_capture/restore/rotate event.
    # Worker-side _snapshot_gpu_mem; runs in main worker thread (no race).
    # Gated on --profile-mem so non-profiling runs are bit-identical.
    if profile_mem:
        os.environ["PAPER2_GPU_MEM_TRACE_PATH"] = str(out_dir / "gpu_mem_trace.jsonl")
    else:
        os.environ.pop("PAPER2_GPU_MEM_TRACE_PATH", None)

    # Wire env knobs that validate_recall.py reads on import.
    os.environ["PAPER2_INSTANCES"] = instances
    os.environ["PAPER2_N_SEEDS"] = str(n_seeds)
    os.environ["PAPER2_TEMPERATURE"] = str(temperature)
    os.environ["PAPER2_RECALL_VARIANTS"] = variants
    os.environ["PAPER2_MAX_STEPS"] = str(max_steps)
    os.environ["PAPER2_GPU_UTIL"] = str(gpu_util)
    os.environ["PAPER2_MAX_LEN"] = str(max_model_len)
    os.environ["PAPER2_OUT_DIR"] = str(out_dir)
    os.environ["PAPER2_ATTENTION_MASK_MODE"] = "1" if attention_mask_mode else "0"
    os.environ["PAPER2_BUDGET_TOKENS"] = str(budget_tokens)
    os.environ["PAPER2_HARD_BUDGET_TOKENS"] = str(hard_budget_tokens)
    os.environ["PAPER2_RECALL_LOW_WATER"] = str(recall_low_water)
    if no_pin:
        os.environ["PAPER2_NO_PIN"] = "1"
    else:
        os.environ.pop("PAPER2_NO_PIN", None)

    print(f"[modal] run_id={run_id}")
    print(f"[modal] instances={instances}")
    print(f"[modal] n_seeds={n_seeds} temperature={temperature}")
    print(f"[modal] variants={variants}")
    print(f"[modal] attention_mask_mode={attention_mask_mode}")
    print(f"[modal] out_dir={out_dir}")

    t0 = time.perf_counter()
    # Import after env is set — the module reads env at module scope.
    from studies.lifetime_cost.paper2 import validate_recall as vr
    vr.main()
    wall_s = time.perf_counter() - t0

    # Collect summary file content.
    summary_path = out_dir / "validate_recall_summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else None

    # Persist any volume changes back to Modal storage.
    from .image import out_volume, hf_volume, swebench_volume
    out_volume.commit()
    hf_volume.commit()
    swebench_volume.commit()

    print(f"[modal] DONE in {wall_s:.1f}s — wrote {summary_path}")
    return {
        "run_id": run_id,
        "wall_s": wall_s,
        "out_dir": str(out_dir),
        "rows": summary,
    }


@app.local_entrypoint()
def main(
    instances: str = "pytest-dev__pytest-7490",
    n_seeds: int = 3,
    temperature: float = 0.6,
    variants: str = "off,lru,lru-append,embedding,embedding-append",
    max_steps: int = 20,
    attention_mask_mode: bool = False,
    budget_tokens: int = 24_000,
    hard_budget_tokens: int = 30_000,
    recall_low_water: float = 0.60,
    no_pin: bool = False,
    profile_mem: bool = False,
    max_model_len: int = 65_000,
):
    """CLI entrypoint — `modal run` calls this."""
    result = run_validate_recall.remote(
        instances=instances,
        n_seeds=n_seeds,
        temperature=temperature,
        variants=variants,
        max_steps=max_steps,
        attention_mask_mode=attention_mask_mode,
        budget_tokens=budget_tokens,
        hard_budget_tokens=hard_budget_tokens,
        recall_low_water=recall_low_water,
        no_pin=no_pin,
        profile_mem=profile_mem,
        max_model_len=max_model_len,
    )
    print()
    print("=" * 70)
    print(f"Modal bake DONE — run_id={result['run_id']}  wall={result['wall_s']:.1f}s")
    print(f"Output volume path:   {result['out_dir']}")
    print()
    print("Fetch results locally:")
    print(f"  modal volume get paper2-out-v3 / ./modal_out_v3")
    print()
    rows = result.get("rows") or []
    if not rows:
        print("(no summary rows — check container logs)")
        return
    print(f"{'task':<32} {'variant':<18} {'seed':>4} {'steps':>5} {'res':>4} "
          f"{'recalls':>7} {'compact':>7} {'wall_s':>7}")
    for r in rows:
        print(f"{r['task_id'][:32]:<32} {r['variant']:<18} {r['seed']:>4} "
              f"{r['steps']:>5} {str(r['resolved'])[:4]:>4} "
              f"{r['num_recalls']:>7} {r['num_compactions']:>7} "
              f"{r['total_wall_ms']/1000:>7.1f}")
