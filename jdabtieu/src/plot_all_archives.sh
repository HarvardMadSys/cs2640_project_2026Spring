#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

shopt -s nullglob

# look for archives inside the "archives" subdirectory
archives=("${SCRIPT_DIR}/archives"/*.tar.gz)
if (( ${#archives[@]} == 0 )); then
  printf 'No .tar.gz archives found in %s/archives\n' "$SCRIPT_DIR" >&2
  exit 0
fi

for archive in "${archives[@]}"; do
  base_name=$(basename -- "$archive")
  run_name=${base_name%.tar.gz}
  run_name=${run_name##*_}
  run_dir="${SCRIPT_DIR}/${run_name}"

  mkdir -p "$run_dir"
  tar -xzf "$archive" -C "$run_dir"

  # workload is the suffix after the final '-'
  workload=${run_name##*-}

  if [[ "$workload" == "varmail" ]]; then
    fb_stdout="$run_dir/results/filebench.stdout.txt"
    if [[ -f "$fb_stdout" ]]; then
      python "$SCRIPT_DIR/scripts/plot_filebench.py" "$fb_stdout" all
    else
      printf 'Warning: %s not found for %s\n' "$fb_stdout" "$run_name" >&2
    fi
  else
    if [[ "$workload" == "randread" || "$workload" == "randwrite" ]]; then
      workload="${workload}4k"
    fi
    json_path="$run_dir/results/${workload}.json"
    if [[ -f "$json_path" ]]; then
      python "$SCRIPT_DIR/scripts/plot_fsbench.py" "$json_path" all
    else
      printf 'Warning: %s not found for %s\n' "$json_path" "$run_name" >&2
    fi
  fi
done