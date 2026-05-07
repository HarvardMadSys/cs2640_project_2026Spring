#!/bin/bash
#SBATCH --job-name=qwen36_combined
#SBATCH --partition=gpu_requeue
#SBATCH --time=01:30:00
#SBATCH --gres=gpu:1
#SBATCH --constraint="a100|h100|h200"
#SBATCH --cpus-per-task=8
#SBATCH --mem=80G
#SBATCH --output=/n/netscratch/idreos_lab/Lab/vcainamisir/dl_logs/qwen36_combined.%j.log

# One-shot: load Qwen3.6-27B-FP8 once, run smoke probe (idle/32K VRAM,
# tokens/sec) AND the HF past_key_values reuse probe (warm vs cold for
# the same B prompt that shares a prefix with A) in a single Python
# process so we pay the model-load cost only once.
#
# Reads:
#   /n/netscratch/idreos_lab/Lab/vcainamisir/hf_cache/...Qwen3.6-27B-FP8/...
# Writes:
#   /n/netscratch/idreos_lab/Lab/vcainamisir/dl_logs/qwen36_combined.<jobid>.log
#   paper/paper2/out/{smoke.json, hf_kv_reuse.json}
#
# Environment:
#   transformers 5.6.2 + huggingface_hub 1.12 layered via PYTHONPATH
#   onto the lora env (torch 2.7.1+cu126).

set -euo pipefail

export HF_HOME=/n/netscratch/idreos_lab/Lab/vcainamisir/hf_cache
export PYTHONPATH=/n/netscratch/idreos_lab/Lab/vcainamisir/py_extra_tf5${PYTHONPATH:+:$PYTHONPATH}

PY=${PY:-/n/home06/vcainamisir/micromamba/envs/lora/bin/python}
ROOT=/net/rcstorenfs02/ifs/rc_labs/idreos_lab/users/vcainamisir/adaptivecache

OUT=$ROOT/paper/paper2/out
mkdir -p $OUT

echo "[$(date)] starting combined probe"
nvidia-smi -L || true
free -g

# Smoke first (it tears down its own model state by exit; we'll launch
# the kv-reuse probe as a separate process to keep them isolated and
# easy to debug).
echo
echo "===== smoke ====="
$PY $ROOT/paper/paper2/scripts/qwen36_smoke.py --skip-long || echo "SMOKE_FAIL"

echo
echo "===== smoke 32K ====="
$PY $ROOT/paper/paper2/scripts/qwen36_smoke.py || echo "SMOKE_FAIL_LONG"

echo
echo "===== hf kv reuse ====="
$PY $ROOT/paper/paper2/scripts/hf_kv_reuse_probe.py \
    --out $OUT/hf_kv_reuse.json || echo "KVREUSE_FAIL"

echo "[$(date)] done."
