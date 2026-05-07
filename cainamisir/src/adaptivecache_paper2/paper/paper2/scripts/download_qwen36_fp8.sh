#!/bin/bash
#SBATCH --job-name=qwen36_fp8_dl
#SBATCH --partition=shared
#SBATCH --time=02:30:00
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --output=/n/netscratch/idreos_lab/Lab/vcainamisir/dl_logs/qwen36_fp8_dl.%j.log

# Download Qwen3.6-27B-FP8 to the shared HF cache.
# ~28 GB. Resumable. Idempotent.
#
# The base BF16 model (Qwen/Qwen3.6-27B) is ~54 GB which is harder to fit on
# a single 80GB A100 alongside KV cache + activations; FP8 is the right SKU
# for our gpu_requeue allocation. We share the cache root so the previously
# downloaded tokenizer/config can be reused.

set -euo pipefail

export HF_HOME=/n/netscratch/idreos_lab/Lab/vcainamisir/hf_cache
export HUGGINGFACE_HUB_DOWNLOAD_TIMEOUT=600

PY=/n/home06/vcainamisir/micromamba/envs/lora/bin/python

echo "[$(date)] start download Qwen/Qwen3.6-27B-FP8 -> $HF_HOME"

$PY - <<'PYEOF'
import os
from huggingface_hub import snapshot_download
path = snapshot_download(
    repo_id="Qwen/Qwen3.6-27B-FP8",
    cache_dir=os.environ["HF_HOME"] + "/hub",
    resume_download=True,
    max_workers=4,
)
print("DONE", path)
PYEOF

echo "[$(date)] download complete"
du -sh /n/netscratch/idreos_lab/Lab/vcainamisir/hf_cache/hub/models--Qwen--Qwen3.6-27B-FP8/ || true
