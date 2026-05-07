#!/usr/bin/env bash
# Wrapper that activates the pyenv conda environment and runs the partition script.
# Usage:
#   bash run.sh                         # defaults: 1M wiki, 2000 queries
#   bash run.sh --num-wiki 500000       # override any flag
#   bash run.sh --reuse                 # skip stages whose outputs already exist
#   bash run.sh --devices cuda:0,cuda:2 # multi-GPU embedding (skips GPU 1)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck disable=SC1091
source /home/yunjia/miniconda3/etc/profile.d/conda.sh
conda activate pyenv

cd "$HERE"
exec python prepare_wiki_partitions.py "$@"
