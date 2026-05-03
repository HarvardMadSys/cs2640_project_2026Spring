"""Modal entrypoint: GPU smoke test for v3 Phase 1 KV capture.

Validates the full chain: scheduler → kv_cache_manager → SchedulerOutput →
worker._execute_kv_capture_operations → global_memento_store.

Quick start:

    cd /home/vlad/adaptivecache-paper2
    /home/vlad/adaptivecache/.venv/bin/modal run studies.lifetime_cost.paper2.modal_app.run_smoke_v3_capture
"""
from __future__ import annotations

import time
import modal

from .image import image, standard_volumes


app = modal.App("paper2-smoke-v3-capture")


try:
    secret = modal.Secret.from_dotenv("/home/vlad/adaptivecache/.env")
except Exception:
    secret = modal.Secret.from_name("paper2-anthropic", required_keys=["ANTHROPIC_API_KEY"])


@app.function(
    gpu="H100",
    image=image,
    volumes=standard_volumes(),
    timeout=60 * 30,
    secrets=[secret],
)
def run_smoke() -> dict:
    """Run the v3 capture smoke test."""
    import os
    import sys
    from pathlib import Path

    REPO = "/opt/adaptivecache"
    sys.path.insert(0, REPO)
    os.chdir(REPO)

    os.environ.setdefault("PAPER2_GPU_UTIL", "0.92")
    os.environ.setdefault("PAPER2_MAX_LEN", "32000")

    print("[modal] Starting v3 Phase 1 capture smoke test ...")
    t0 = time.perf_counter()

    # Run the smoke as a subprocess so its sys.exit doesn't kill our function.
    import subprocess
    proc = subprocess.run(
        [
            sys.executable, "-m",
            "studies.lifetime_cost.paper2.tests.smoke_v3_capture",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60 * 25,
    )
    wall_s = time.perf_counter() - t0

    print("[modal] STDOUT:")
    print(proc.stdout)
    if proc.stderr:
        print("[modal] STDERR:")
        print(proc.stderr)

    return {
        "wall_s": wall_s,
        "exit_code": proc.returncode,
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-1000:] if proc.stderr else "",
    }


@app.local_entrypoint()
def main():
    result = run_smoke.remote()
    print()
    print("=" * 70)
    print(f"v3 capture smoke DONE — wall={result['wall_s']:.1f}s  "
          f"exit={result['exit_code']}")
    if result["exit_code"] == 0:
        print("PASS — captures landed in MementoStore.")
    else:
        print("FAIL or NEEDS-PHASE-3 — see output above for diagnostic.")
        print("(Worker-side store may live in a different process; that's a")
        print(" Phase 3 surface, not a Phase 1 bug.)")
