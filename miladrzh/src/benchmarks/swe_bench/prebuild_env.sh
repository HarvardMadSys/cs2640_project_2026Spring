#!/bin/bash
# Pre-build a per-repo conda env for SWE-bench tasks.
#
# Creates a Python 3.11 conda env per repo at agent-os/swe_data/envs/<repo_key>/
# with build deps + pytest installed. Per-task setup_workspace() then runs
# `pip install -e . --no-build-isolation` from the workspace into this env, so
# the agent's edits take effect. First task per repo does the full extension
# build; subsequent tasks reuse pip's wheel cache and ccache.
#
# All paths are relative to the agent-os repo (no $SCRATCH magic). Override
# the data root with SWE_BENCH_DATA=/some/path if you want it elsewhere.
#
# Usage:
#   bash benchmarks/swe_bench/prebuild_env.sh matplotlib
#   bash benchmarks/swe_bench/prebuild_env.sh astropy
#   bash benchmarks/swe_bench/prebuild_env.sh all

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DATA_DIR="${SWE_BENCH_DATA:-$REPO_ROOT/swe_data}"
REPOS_DIR="$DATA_DIR/repos"
ENVS_DIR="$DATA_DIR/envs"
mkdir -p "$REPOS_DIR" "$ENVS_DIR"

clone_if_missing() {
    local url="$1" dest="$2"
    if [ -d "$dest/.git" ]; then
        echo "  base repo already cloned: $dest"
    else
        echo "  cloning $url -> $dest"
        git clone "$url" "$dest"
    fi
}

build_env() {
    local repo_key="$1"; shift
    local repo_url="$1"; shift
    local extra_pkgs=("$@")

    local repo_dir="$REPOS_DIR/$repo_key"
    local env_dir="$ENVS_DIR/$repo_key"

    clone_if_missing "$repo_url" "$repo_dir"

    if [ -x "$env_dir/bin/python" ]; then
        echo "[$repo_key] env already exists at $env_dir — skipping create"
    else
        echo "[$repo_key] creating Python 3.11 conda env at $env_dir"
        conda create -p "$env_dir" -y -c conda-forge python=3.11
    fi

    echo "[$repo_key] installing pip + build deps"
    # Pin setuptools <70: 2021-2022 SWE-bench commits import setuptools.dep_util
    # which was removed in setuptools 70. Wheel + pip we keep latest.
    "$env_dir/bin/pip" install --upgrade pip wheel
    "$env_dir/bin/pip" install "setuptools<70"
    "$env_dir/bin/pip" install \
        "numpy>=1.21,<2" \
        cython \
        pytest pytest-xdist pytest-timeout \
        "${extra_pkgs[@]}"

    echo "[$repo_key] env ready: $env_dir"
}

build_matplotlib() {
    build_env matplotlib__matplotlib \
        https://github.com/matplotlib/matplotlib \
        "cycler>=0.10" "kiwisolver>=1.0.1" "pillow>=6.2" "pyparsing>=2.2.1,<3" \
        "python-dateutil>=2.7" "fonttools>=4.22.0" "packaging>=20.0" \
        "contourpy>=1.0.1" "certifi>=2020.06.20" setuptools_scm
}

build_astropy() {
    build_env astropy__astropy \
        https://github.com/astropy/astropy \
        "pyerfa>=2" "PyYAML>=3.13" "packaging>=19.0" pytest-astropy \
        extension-helpers setuptools_scm
}

case "${1:-all}" in
    matplotlib) build_matplotlib ;;
    astropy)    build_astropy ;;
    all)        build_matplotlib; build_astropy ;;
    *) echo "Usage: $0 [matplotlib|astropy|all]"; exit 1 ;;
esac

echo ""
echo "Done. Data root: $DATA_DIR"
