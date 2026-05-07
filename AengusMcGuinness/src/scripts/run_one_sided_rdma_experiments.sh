#!/usr/bin/env bash
# Run all one-sided RDMA client-side benchmark sweeps from the client node.
#
# Assumes the one-sided RDMA server is already running on the server node:
#   ./build/kv_server_rdma --mode one-sided --device mlx5_3 --port 9091 --preload 1024

set -euo pipefail

HOST="${SERVER_IP:-}"
PORT="9091"
DEVICE="mlx5_3"
CLIENTS="1 2 4 8"
OPS="10000"
KEYS="1024"
WARMUP="1000"
OUTDIR="experiments"
BUILD_DIR="build"
NO_METADATA_CSV=""
METADATA_CSV=""
RESET=0
DRY_RUN=0
ONLY_NO_METADATA=0
ONLY_METADATA=0
CPU_SSH=""
CPU_REMOTE_DIR=""
CPU_CSV=""
CPU_INTERVAL="0.20"
SERVER_PID=""
SERVER_PROCESS="kv_server_rdma"
NET_SSH=""
NETDEV=""
NET_SERVER_IP=""
NET_CSV=""
METRICS_CONTROL=""
METRICS_PORT="19191"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CPU_REMOTE_DIR="$PROJECT_DIR"
source "$SCRIPT_DIR/experiment_cpu.sh"

usage() {
  cat <<'EOF'
Usage: scripts/run_one_sided_rdma_experiments.sh --host SERVER_IP [options]

Runs the one-sided RDMA sweeps and writes:
  experiments/rdma_one_sided_clients.csv
  experiments/rdma_one_sided_metadata.csv

Options:
  --host HOST          Server private-link IP. Defaults to $SERVER_IP.
  --port PORT          Server port. Default: 9091.
  --device NAME        RDMA device. Default: mlx5_3.
  --clients LIST       Client-count sweep values. Default: "1 2 4 8".
                       Commas are also accepted: "1,2,4,8".
  --ops N              Timed operations per run. Default: 10000.
  --keys N             Key count. Default: 1024.
  --warmup N           Warmup operations per run. Default: 1000.
  --outdir DIR         Output directory. Default: experiments.
  --build-dir DIR      Build directory. Default: build.
  --only-no-metadata   Run only pure RDMA READ benchmark.
  --only-metadata      Run only RDMA READ + FETCH_AND_ADD benchmark.
  --cpu-ssh USER@HOST  SSH target for the server node; enables CPU sampling.
  --cpu-remote-dir DIR Server-node project dir. Default: this checkout's absolute path.
  --cpu-csv PATH       Server-node CPU CSV path. Default: experiments/cpu_utilization.csv.
  --cpu-interval SEC   CPU sample interval. Default: 0.20.
  --server-pid PID     Existing server PID to sample. Default: pgrep -n -x kv_server_rdma.
  --server-process N   Process name for pgrep. Default: kv_server_rdma.
  --net-ssh USER@HOST  SSH target for the server node; enables NIC bytes/op.
  --netdev DEV         Server NIC to sample. Default: auto-detect from --host.
  --net-server-ip IP   Server IP used for NIC auto-detection. Default: --host.
  --net-csv PATH       Local NIC CSV path. Default: OUTDIR/network_utilization.csv.
  --metrics-ssh TARGET Convenience: sets both --cpu-ssh and --net-ssh.
  --metrics-control H  Metrics collector host or host:port. No SSH required.
  --metrics-port PORT  Metrics collector port. Default: 19191.
  --reset              Delete this script's CSVs before running.
  --dry-run            Print commands without running them.
  -h, --help           Show this help text.
EOF
}

die() {
  echo "run_one_sided_rdma_experiments: $*" >&2
  exit 2
}

print_cmd() {
  printf '  '
  printf '%q ' "$@"
  printf '\n'
}

run_cmd() {
  print_cmd "$@"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    "$@"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --device) DEVICE="${2:-}"; shift 2 ;;
    --clients) CLIENTS="${2:-}"; shift 2 ;;
    --ops) OPS="${2:-}"; shift 2 ;;
    --keys) KEYS="${2:-}"; shift 2 ;;
    --warmup) WARMUP="${2:-}"; shift 2 ;;
    --outdir) OUTDIR="${2:-}"; shift 2 ;;
    --build-dir) BUILD_DIR="${2:-}"; shift 2 ;;
    --only-no-metadata) ONLY_NO_METADATA=1; shift ;;
    --only-metadata) ONLY_METADATA=1; shift ;;
    --cpu-ssh) CPU_SSH="${2:-}"; shift 2 ;;
    --cpu-remote-dir) CPU_REMOTE_DIR="${2:-}"; shift 2 ;;
    --cpu-csv) CPU_CSV="${2:-}"; shift 2 ;;
    --cpu-interval) CPU_INTERVAL="${2:-}"; shift 2 ;;
    --server-pid) SERVER_PID="${2:-}"; shift 2 ;;
    --server-process) SERVER_PROCESS="${2:-}"; shift 2 ;;
    --net-ssh) NET_SSH="${2:-}"; shift 2 ;;
    --netdev) NETDEV="${2:-}"; shift 2 ;;
    --net-server-ip) NET_SERVER_IP="${2:-}"; shift 2 ;;
    --net-csv) NET_CSV="${2:-}"; shift 2 ;;
    --metrics-ssh) CPU_SSH="${2:-}"; NET_SSH="${2:-}"; shift 2 ;;
    --metrics-control) METRICS_CONTROL="${2:-}"; shift 2 ;;
    --metrics-port) METRICS_PORT="${2:-}"; shift 2 ;;
    --reset) RESET=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ -n "$HOST" ]] || die "pass --host SERVER_IP or set SERVER_IP"
[[ -n "$CLIENTS" ]] || die "--clients cannot be empty"
if [[ "$ONLY_NO_METADATA" -eq 1 && "$ONLY_METADATA" -eq 1 ]]; then
  die "choose at most one of --only-no-metadata and --only-metadata"
fi

CLIENT="$BUILD_DIR/kv_client_rdma"
if [[ "$DRY_RUN" -eq 0 && ! -x "$CLIENT" ]]; then
  die "missing executable: $CLIENT"
fi

mkdir -p "$OUTDIR"
NO_METADATA_CSV="${NO_METADATA_CSV:-$OUTDIR/rdma_one_sided_clients.csv}"
METADATA_CSV="${METADATA_CSV:-$OUTDIR/rdma_one_sided_metadata.csv}"
NET_CSV="${NET_CSV:-$OUTDIR/network_utilization.csv}"
CPU_CSV="${CPU_CSV:-$OUTDIR/cpu_utilization.csv}"

if [[ "$RESET" -eq 1 ]]; then
  if [[ "$DRY_RUN" -eq 1 ]]; then
    if [[ "$ONLY_METADATA" -eq 0 ]]; then
      echo "dry-run: would remove $NO_METADATA_CSV"
    fi
    if [[ "$ONLY_NO_METADATA" -eq 0 ]]; then
      echo "dry-run: would remove $METADATA_CSV"
    fi
  else
    if [[ "$ONLY_METADATA" -eq 0 ]]; then
      rm -f "$NO_METADATA_CSV"
    fi
    if [[ "$ONLY_NO_METADATA" -eq 0 ]]; then
      rm -f "$METADATA_CSV"
    fi
  fi
fi

echo "One-sided RDMA sweeps"
echo "  host=$HOST port=$PORT device=$DEVICE clients=${CLIENTS//,/ } outdir=$OUTDIR"

if [[ "$ONLY_METADATA" -eq 0 ]]; then
  for C in ${CLIENTS//,/ }; do
    echo
    echo "== One-sided RDMA no metadata: $C clients =="
    run_with_optional_metrics "One-Sided RDMA" "one_sided_rdma" "$C" 0 "$OPS" "$CLIENT" \
      --host "$HOST" --port "$PORT" \
      --mode one-sided --device "$DEVICE" \
      --benchmark --clients "$C" \
      --ops "$OPS" --warmup "$WARMUP" --keys "$KEYS" \
      --csv "$NO_METADATA_CSV"
  done
fi

if [[ "$ONLY_NO_METADATA" -eq 0 ]]; then
  for C in ${CLIENTS//,/ }; do
    echo
    echo "== One-sided RDMA metadata: $C clients =="
    run_with_optional_metrics "One-Sided RDMA + Metadata" "one_sided_rdma_metadata" "$C" 1 "$OPS" "$CLIENT" \
      --host "$HOST" --port "$PORT" \
      --mode one-sided --device "$DEVICE" \
      --benchmark --clients "$C" \
      --ops "$OPS" --warmup "$WARMUP" --keys "$KEYS" \
      --metadata \
      --csv "$METADATA_CSV"
  done
fi

cpu_sync_csv "$OUTDIR"

echo
echo "One-sided RDMA experiments complete:"
if [[ "$ONLY_METADATA" -eq 0 ]]; then
  echo "  $NO_METADATA_CSV"
fi
if [[ "$ONLY_NO_METADATA" -eq 0 ]]; then
  echo "  $METADATA_CSV"
fi
