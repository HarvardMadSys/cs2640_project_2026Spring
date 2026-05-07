#!/bin/bash
#SBATCH --partition=seas_gpu
#SBATCH --nodes=1
#SBATCH --gres=gpu:nvidia_a100-sxm4-80gb:1
#SBATCH --cpus-per-task=16
#SBATCH --mem=128G
#SBATCH --time=08:00:00

# One sweep point: runs benchmark.py at concurrency=$SWEEP_N on the curated
# 512-task pool, Qwen2.5-7B-Instruct, A100 80GB. Submitted via
# benchmarks/slurm/submit_sweep.sh (which sets --output, --error, --job-name,
# and exports SWEEP_N). To call by hand:
#
#   sbatch --export=ALL,SWEEP_N=8 \
#       --job-name=agent-os-N8 \
#       --output=logs/seas_N8_%j.out \
#       --error=logs/seas_N8_%j.err \
#       benchmarks/slurm/run_a100_one_n.sh

set -euo pipefail

if [[ -z "${SWEEP_N:-}" ]]; then
    echo "ERROR: SWEEP_N env var not set. Use submit_sweep.sh." >&2
    exit 2
fi

module load cuda/12.4.1-fasrc01
module load cudnn/9.10.2.21_cuda12-fasrc01

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

export SCRATCH="${SCRATCH:-/n/netscratch/idreos_lab/Lab/milad}"
export HF_HOME="${SCRATCH}/hf_cache"
export HF_HUB_CACHE="${HF_HOME}/hub"
mkdir -p "$HF_HOME" "$HF_HUB_CACHE" logs

source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate rag

MODEL="Qwen/Qwen2.5-7B-Instruct"
TASKS_FILE="benchmarks/_sweep512.json"
OUT_DIR="traces/a100_7B_N${SWEEP_N}"
mkdir -p "$OUT_DIR"

echo "=== N=${SWEEP_N} start: $(date -Is) (job ${SLURM_JOB_ID:-?}) ==="
echo "node: $(hostname)"
echo "model: $MODEL"
echo "out:   $OUT_DIR"
nvidia-smi | head -20 || true
echo

python -u benchmark.py \
    --tasks-file "$TASKS_FILE" \
    --output-dir "$OUT_DIR" \
    --model "$MODEL" \
    --engine local \
    --max-model-len 32768 \
    --gpu-memory-utilization 0.85 \
    --max-turns 200 \
    --concurrency "$SWEEP_N"

echo "=== N=${SWEEP_N} done: $(date -Is) ==="
