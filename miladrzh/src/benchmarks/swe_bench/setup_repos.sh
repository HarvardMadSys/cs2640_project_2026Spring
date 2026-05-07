#!/bin/bash
# Clone the SWE-bench repos to $SCRATCH once, on the login node.
# Each per-task workspace is then created with `git clone --local` (fast, uses hardlinks).
#
# Run once before submitting SLURM jobs:
#   bash benchmarks/swe_bench/setup_repos.sh

set -euo pipefail

REPOS_DIR="${SCRATCH:-/tmp}/swe_repos"
mkdir -p "$REPOS_DIR"

clone_if_missing() {
    local url="$1"
    local dest="$2"
    if [ -d "$dest/.git" ]; then
        echo "  already cloned: $dest"
    else
        echo "  cloning $url -> $dest"
        git clone --depth=1000 "$url" "$dest"
    fi
}

echo "Cloning SWE-bench repos to $REPOS_DIR ..."

clone_if_missing https://github.com/sympy/sympy           "$REPOS_DIR/sympy__sympy"
clone_if_missing https://github.com/scikit-learn/scikit-learn "$REPOS_DIR/scikit-learn__scikit-learn"
clone_if_missing https://github.com/matplotlib/matplotlib "$REPOS_DIR/matplotlib__matplotlib"
clone_if_missing https://github.com/astropy/astropy       "$REPOS_DIR/astropy__astropy"

echo ""
echo "Done. Repos are in $REPOS_DIR"
echo "Next: python benchmarks/swe_bench/pick_tasks.py"
