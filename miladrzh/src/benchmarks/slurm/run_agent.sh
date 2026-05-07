#!/bin/bash
#SBATCH --job-name=agent-os
#SBATCH --partition=gpu
#SBATCH --nodes=1
#SBATCH --gres=gpu:nvidia_a100-sxm4-80gb:4
#SBATCH --cpus-per-task=32
#SBATCH --mem=256G
#SBATCH --time=24:00:00
#SBATCH --output=logs/slurm_%j.out
#SBATCH --error=logs/slurm_%j.err

# agent-os benchmark run on FASRC (Harvard cluster).
# vLLM runs embedded as a Python library (--engine local) — no separate server needed.
#
# Submit with: sbatch benchmarks/slurm/run_agent.sh
# Edit MODEL below to switch between Llama 3.1 8B and 3.3 70B AWQ.

set -euo pipefail

module load cuda/12.4.1-fasrc01
module load cudnn/9.10.2.21_cuda12-fasrc01

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

export HF_HOME="${SCRATCH}/hf_cache"
export HF_HUB_CACHE="${HF_HOME}/hub"
mkdir -p "$HF_HOME" "$HF_HUB_CACHE" "$REPO_ROOT/logs" "$REPO_ROOT/traces"

cd "$REPO_ROOT"

source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate rag

MODEL="meta-llama/Llama-3.1-8B-Instruct"

echo "[$(date)] Running data_analysis benchmark (engine=local, 4 GPUs): $MODEL"

python run.py \
  --dataset data_analysis \
  --model "$MODEL" \
  --engine local \
  --tensor-parallel-size 4 \
  --max-turns 12

echo "[$(date)] Done. Traces saved to traces/"
