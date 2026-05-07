#!/bin/bash
#SBATCH --job-name=qwen36_smoke
#SBATCH --partition=gpu_requeue
#SBATCH --time=00:45:00
#SBATCH --gres=gpu:1
#SBATCH --constraint=a100
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --output=/n/netscratch/idreos_lab/Lab/vcainamisir/dl_logs/qwen36_smoke.%j.log

# Smoke probe: load Qwen3.6-27B-FP8 in text-only mode on one A100, run
# generate() on a short prompt and a 32K prompt. Reports VRAM and tokens/sec.
#
# Pre-req: download_qwen36_fp8.sh must have completed.
# Pre-req: a python env with transformers >= 5.6 (the lora env at the time
#   this was submitted has 4.57 which does NOT support qwen3_5; see
#   qwen36_findings.md "Environment gap" section).

set -euo pipefail

export HF_HOME=/n/netscratch/idreos_lab/Lab/vcainamisir/hf_cache
# Layered transformers 5.6.2 + huggingface_hub 1.12 sit in this target dir.
# Putting them on PYTHONPATH lets us reuse the lora env's torch + cuda
# without breaking it for the main agent.
export PYTHONPATH=/n/netscratch/idreos_lab/Lab/vcainamisir/py_extra_tf5${PYTHONPATH:+:$PYTHONPATH}

PY=${PY:-/n/home06/vcainamisir/micromamba/envs/lora/bin/python}
SCRIPT=/net/rcstorenfs02/ifs/rc_labs/idreos_lab/users/vcainamisir/adaptivecache/paper/paper2/scripts/qwen36_smoke.py

echo "[$(date)] running smoke probe with PY=$PY"
echo "PYTHONPATH=$PYTHONPATH"
nvidia-smi -L || true
$PY $SCRIPT "$@"
echo "[$(date)] done"
