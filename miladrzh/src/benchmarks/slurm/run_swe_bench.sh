#!/bin/bash
#SBATCH --job-name=swe-bench
#SBATCH --partition=gpu
#SBATCH --nodes=1
#SBATCH --gres=gpu:nvidia_a100-sxm4-80gb:1
#SBATCH --cpus-per-task=16
#SBATCH --mem=128G
#SBATCH --time=12:00:00
#SBATCH --output=logs/swe_bench_%j.out
#SBATCH --error=logs/swe_bench_%j.err

# SWE-bench benchmark run on FASRC (no Docker/Singularity).
# Repos must be pre-cloned before submitting:
#   bash benchmarks/swe_bench/setup_repos.sh
#   python benchmarks/swe_bench/pick_tasks.py
#
# Submit with: sbatch benchmarks/slurm/run_swe_bench.sh

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

echo "[$(date)] Running SWE-bench (engine=local, 1 GPU): $MODEL"

# Smoke-test one task first:
# python run.py --dataset swe_bench --task sympy__sympy-12419 \
#   --model "$MODEL" --engine local --max-turns 16

python run.py \
  --dataset swe_bench \
  --model "$MODEL" \
  --engine local \
  --tensor-parallel-size 1 \
  --max-turns 16

echo "[$(date)] Done. Traces saved to traces/"
