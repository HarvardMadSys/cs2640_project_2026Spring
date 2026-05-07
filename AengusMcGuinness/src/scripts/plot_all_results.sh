#!/usr/bin/env bash
# Regenerate every report plot from the CSV files currently in experiments/.

set -euo pipefail

PYTHON="${PYTHON:-python3}"
EXPERIMENTS_DIR="experiments"
PLOTS_DIR="plots"

usage() {
  cat <<'EOF'
Usage: scripts/plot_all_results.sh [options]

Regenerates all benchmark, comparison, metadata, and CPU plots that have
matching CSV files in experiments/.

Options:
  --experiments-dir DIR   CSV directory. Default: experiments.
  --plots-dir DIR         Plot output directory. Default: plots.
  --python PATH           Python executable. Default: python3 or $PYTHON.
  -h, --help              Show this help text.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --experiments-dir) EXPERIMENTS_DIR="${2:-}"; shift 2 ;;
    --plots-dir) PLOTS_DIR="${2:-}"; shift 2 ;;
    --python) PYTHON="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "plot_all_results: unknown option: $1" >&2; exit 2 ;;
  esac
done

export MPLBACKEND="${MPLBACKEND:-Agg}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/mplconfig}"
mkdir -p "$MPLCONFIGDIR" "$PLOTS_DIR"

run_if_present() {
  local csv_path="$1"
  local outdir="$2"
  local x_axis="$3"
  local title="$4"

  if [[ ! -s "$csv_path" ]]; then
    echo "skip: missing $csv_path"
    return
  fi

  "$PYTHON" scripts/plot_benchmark.py \
    --csv "$csv_path" \
    --outdir "$outdir" \
    --x "$x_axis" \
    --title-prefix "$title"
}

echo "Generating individual benchmark plots..."
run_if_present "$EXPERIMENTS_DIR/tcp_cloudlab_clients.csv" \
  "$PLOTS_DIR/tcp_cloudlab" clients "TCP (CloudLab)"
run_if_present "$EXPERIMENTS_DIR/rdma_two_sided_clients.csv" \
  "$PLOTS_DIR/rdma_two_sided_clients" clients "Two-Sided RDMA"
run_if_present "$EXPERIMENTS_DIR/rdma_two_sided_ratio.csv" \
  "$PLOTS_DIR/rdma_two_sided_ratio" get_ratio "Two-Sided RDMA"
run_if_present "$EXPERIMENTS_DIR/rdma_two_sided_zipf.csv" \
  "$PLOTS_DIR/rdma_two_sided_zipf" zipf_s "Two-Sided RDMA"
run_if_present "$EXPERIMENTS_DIR/rdma_two_sided_valsize.csv" \
  "$PLOTS_DIR/rdma_two_sided_valsize" value_size "Two-Sided RDMA"
run_if_present "$EXPERIMENTS_DIR/rdma_one_sided_clients.csv" \
  "$PLOTS_DIR/rdma_one_sided" clients "One-Sided RDMA"
run_if_present "$EXPERIMENTS_DIR/rdma_one_sided_metadata.csv" \
  "$PLOTS_DIR/rdma_one_sided_metadata" clients "One-Sided RDMA (with LRU metadata)"

echo
echo "Generating comparison plots..."
if [[ -s "$EXPERIMENTS_DIR/tcp_cloudlab_clients.csv" &&
      -s "$EXPERIMENTS_DIR/rdma_two_sided_clients.csv" &&
      -s "$EXPERIMENTS_DIR/rdma_one_sided_clients.csv" ]]; then
  "$PYTHON" scripts/plot_comparison.py \
    --csv "TCP" "$EXPERIMENTS_DIR/tcp_cloudlab_clients.csv" \
    --csv "Two-Sided RDMA" "$EXPERIMENTS_DIR/rdma_two_sided_clients.csv" \
    --csv "One-Sided RDMA" "$EXPERIMENTS_DIR/rdma_one_sided_clients.csv" \
    --x clients \
    --outdir "$PLOTS_DIR/comparison" \
    --title "Transport Comparison"
else
  echo "skip: comparison plots need TCP, two-sided, and one-sided client CSVs"
fi

if [[ -s "$EXPERIMENTS_DIR/rdma_one_sided_clients.csv" &&
      -s "$EXPERIMENTS_DIR/rdma_one_sided_metadata.csv" ]]; then
  "$PYTHON" scripts/plot_comparison.py \
    --csv "One-Sided (no metadata)" "$EXPERIMENTS_DIR/rdma_one_sided_clients.csv" \
    --csv "One-Sided (LRU metadata)" "$EXPERIMENTS_DIR/rdma_one_sided_metadata.csv" \
    --x clients \
    --outdir "$PLOTS_DIR/metadata_overhead" \
    --title "One-Sided RDMA: LRU Metadata Overhead"
else
  echo "skip: metadata plots need both one-sided CSVs:"
  for needed in \
    "$EXPERIMENTS_DIR/rdma_one_sided_clients.csv" \
    "$EXPERIMENTS_DIR/rdma_one_sided_metadata.csv"; do
    if [[ -s "$needed" ]]; then
      echo "  ok: $needed"
    else
      echo "  missing or empty: $needed"
    fi
  done
fi

if [[ -s "$EXPERIMENTS_DIR/cpu_utilization.csv" ]]; then
  "$PYTHON" scripts/plot_cpu.py \
    --csv "$EXPERIMENTS_DIR/cpu_utilization.csv" \
    --outdir "$PLOTS_DIR/cpu" \
    --x clients \
    --title "Server CPU Utilization"
else
  echo "skip: missing $EXPERIMENTS_DIR/cpu_utilization.csv"
  echo "  CPU rows are produced only by scripts/measure_cpu.py on the server node."
fi

if [[ -s "$EXPERIMENTS_DIR/network_utilization.csv" ]]; then
  "$PYTHON" scripts/plot_network.py \
    --csv "$EXPERIMENTS_DIR/network_utilization.csv" \
    --outdir "$PLOTS_DIR/network" \
    --x clients \
    --title "Server NIC Bytes Per Operation"
else
  echo "skip: missing $EXPERIMENTS_DIR/network_utilization.csv"
  echo "  Network rows are produced by runner scripts when --net-ssh or --metrics-ssh is used."
fi

echo
echo "All available plots regenerated under $PLOTS_DIR/"
