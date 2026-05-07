#!/bin/bash
# Submit one SLURM job per sweep point N.
#
# Each job is independent: own jobid, own GPU allocation, own logs.
# Failures of large-N jobs (e.g. KV-pool exhaustion at N=256) do not affect
# the other sweep points.
#
# Usage:
#   bash benchmarks/slurm/submit_sweep.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

mkdir -p logs

SWEEP_NS=(8 16 32 64 128 256)

echo "Submitting ${#SWEEP_NS[@]} jobs to seas_gpu (A100 80GB) ..."
for N in "${SWEEP_NS[@]}"; do
    JID=$(sbatch --parsable \
        --export=ALL,SWEEP_N=${N} \
        --job-name=agent-os-N${N} \
        --output=logs/seas_N${N}_%j.out \
        --error=logs/seas_N${N}_%j.err \
        benchmarks/slurm/run_a100_one_n.sh)
    echo "  N=${N}  -> job ${JID}"
done
echo
echo "Watch with: squeue -u \$USER"
