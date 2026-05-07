"""Modal entrypoint: GPU smoke for Phase 3a restore_pinned_kv (CPU→GPU roundtrip).

Runs the smoke_v3_restore.py test on Modal H100. Verifies the inverse of
Phase 1 capture: bytes from CPU pinned memory get copied back to specific
GPU block IDs, byte-exact.

Quick start:
    cd /home/vlad/adaptivecache-paper2
    /home/vlad/adaptivecache/.venv/bin/modal run -m \\
      studies.lifetime_cost.paper2.modal_app.run_smoke_v3_restore
"""
from __future__ import annotations

import time
import modal

from .image import image, standard_volumes


app = modal.App("paper2-smoke-v3-restore")


try:
    secret = modal.Secret.from_dotenv("/home/vlad/adaptivecache/.env")
except Exception:
    secret = modal.Secret.from_name("paper2-anthropic", required_keys=["ANTHROPIC_API_KEY"])


@app.function(
    gpu="H100",
    image=image,
    volumes=standard_volumes(),
    timeout=60 * 10,
    secrets=[secret],
)
def run_smoke() -> dict:
    import os, sys, subprocess
    REPO = "/opt/adaptivecache"
    sys.path.insert(0, REPO)
    os.chdir(REPO)

    print("[modal] Starting v3 Phase 3a restore smoke ...")
    t0 = time.perf_counter()

    proc = subprocess.run(
        [sys.executable, "-m",
         "studies.lifetime_cost.paper2.tests.smoke_v3_restore"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60 * 8,
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
    }


@app.local_entrypoint()
def main():
    result = run_smoke.remote()
    print()
    print("=" * 70)
    print(f"v3 restore smoke DONE — wall={result['wall_s']:.1f}s  "
          f"exit={result['exit_code']}")
    if result["exit_code"] == 0:
        print("PASS — capture/restore roundtrip is byte-exact.")
    else:
        print("FAIL — see output above.")
