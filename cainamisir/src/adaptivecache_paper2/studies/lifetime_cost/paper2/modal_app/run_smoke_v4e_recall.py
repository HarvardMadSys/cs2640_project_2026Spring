"""Modal entrypoint: GPU smoke for v4 Phase 4e recall via unmask.

Quick start:

    cd /home/vlad/adaptivecache-paper2
    /home/vlad/adaptivecache/.venv/bin/modal run \
        studies.lifetime_cost.paper2.modal_app.run_smoke_v4e_recall
"""
from __future__ import annotations

import time
import modal

from .image import image, standard_volumes


app = modal.App("paper2-smoke-v4e-recall")


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
    import os
    import sys
    import subprocess

    REPO = "/opt/adaptivecache"
    sys.path.insert(0, REPO)
    os.chdir(REPO)

    os.environ.setdefault("PAPER2_GPU_UTIL", "0.92")
    os.environ.setdefault("PAPER2_MAX_LEN", "32000")

    print("[modal] Starting v4 Phase 4e recall smoke ...")
    t0 = time.perf_counter()

    proc = subprocess.run(
        [sys.executable, "-m",
         "studies.lifetime_cost.paper2.tests.smoke_v4e_recall"],
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
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-1000:] if proc.stderr else "",
    }


@app.local_entrypoint()
def main():
    result = run_smoke.remote()
    print()
    print("=" * 70)
    print(f"v4e recall smoke DONE — wall={result['wall_s']:.1f}s  "
          f"exit={result['exit_code']}")
    if result["exit_code"] == 0:
        print("PASS")
    else:
        print("FAIL — see output above.")
