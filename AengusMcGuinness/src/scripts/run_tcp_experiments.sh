#!/usr/bin/env bash
# Run the TCP client-count benchmark sweep from the client node.
#
# Assumes the TCP server is already running on the server node:
#   ./build/kv_server 9090

set -euo pipefail

HOST="${SERVER_IP:-}"
PORT="9090"
CLIENTS="1 2 4 8"
OPS="10000"
KEYS="1024"
VALUE_SIZE="64"
GET_RATIO="0.95"
ZIPF_S="0.8"
WARMUP="1000"
OUTDIR="experiments"
BUILD_DIR="build"
CSV_PATH=""
RESET=0
DRY_RUN=0
CPU_SSH=""
CPU_REMOTE_DIR=""
CPU_CSV=""
CPU_INTERVAL="0.20"
SERVER_PID=""
SERVER_PROCESS="kv_server"
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
Usage: scripts/run_tcp_experiments.sh --host SERVER_IP [options]

Runs the TCP CloudLab client-count sweep and writes:
  experiments/tcp_cloudlab_clients.csv

Options:
  --host HOST          Server private-link IP. Defaults to $SERVER_IP.
  --port PORT          Server port. Default: 9090.
  --clients LIST       Client counts. Default: "1 2 4 8".
                       Commas are also accepted: "1,2,4,8".
  --ops N              Timed operations per run. Default: 10000.
  --keys N             Key count. Default: 1024.
  --value-size N       SET value size. Default: 64.
  --get-ratio R        GET fraction. Default: 0.95.
  --zipf-s S           Zipf skew. Default: 0.8.
  --warmup N           Warmup operations per run. Default: 1000.
  --outdir DIR         Output directory. Default: experiments.
  --csv PATH           CSV output path. Default: OUTDIR/tcp_cloudlab_clients.csv.
  --build-dir DIR      Build directory. Default: build.
  --cpu-ssh USER@HOST  SSH target for the server node; enables CPU sampling.
  --cpu-remote-dir DIR Server-node project dir. Default: this checkout's absolute path.
  --cpu-csv PATH       Server-node CPU CSV path. Default: experiments/cpu_utilization.csv.
  --cpu-interval SEC   CPU sample interval. Default: 0.20.
  --server-pid PID     Existing server PID to sample. Default: pgrep -n -x kv_server.
  --server-process N   Process name for pgrep. Default: kv_server.
  --net-ssh USER@HOST  SSH target for the server node; enables NIC bytes/op.
  --netdev DEV         Server NIC to sample. Default: auto-detect from --host.
  --net-server-ip IP   Server IP used for NIC auto-detection. Default: --host.
  --net-csv PATH       Local NIC CSV path. Default: OUTDIR/network_utilization.csv.
  --metrics-ssh TARGET Convenience: sets both --cpu-ssh and --net-ssh.
  --metrics-control H  Metrics collector host or host:port. No SSH required.
  --metrics-port PORT  Metrics collector port. Default: 19191.
  --reset              Delete this script's CSV before running.
  --dry-run            Print commands without running them.
  -h, --help           Show this help text.
EOF
}

die() {
  echo "run_tcp_experiments: $*" >&2
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
    --clients) CLIENTS="${2:-}"; shift 2 ;;
    --ops) OPS="${2:-}"; shift 2 ;;
    --keys) KEYS="${2:-}"; shift 2 ;;
    --value-size) VALUE_SIZE="${2:-}"; shift 2 ;;
    --get-ratio) GET_RATIO="${2:-}"; shift 2 ;;
    --zipf-s) ZIPF_S="${2:-}"; shift 2 ;;
    --warmup) WARMUP="${2:-}"; shift 2 ;;
    --outdir) OUTDIR="${2:-}"; shift 2 ;;
    --csv) CSV_PATH="${2:-}"; shift 2 ;;
    --build-dir) BUILD_DIR="${2:-}"; shift 2 ;;
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

BENCHMARK="$BUILD_DIR/kv_benchmark"
if [[ "$DRY_RUN" -eq 0 && ! -x "$BENCHMARK" ]]; then
  die "missing executable: $BENCHMARK"
fi

mkdir -p "$OUTDIR"
if [[ -z "$CSV_PATH" ]]; then
  CSV_PATH="$OUTDIR/tcp_cloudlab_clients.csv"
fi
if [[ -z "$NET_CSV" ]]; then
  NET_CSV="$OUTDIR/network_utilization.csv"
fi
if [[ -z "$CPU_CSV" ]]; then
  CPU_CSV="$OUTDIR/cpu_utilization.csv"
fi

if [[ "$RESET" -eq 1 ]]; then
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "dry-run: would remove $CSV_PATH"
  else
    rm -f "$CSV_PATH"
  fi
fi

echo "TCP client-count sweep"
echo "  host=$HOST port=$PORT clients=${CLIENTS//,/ } csv=$CSV_PATH"

for C in ${CLIENTS//,/ }; do
  echo
  echo "== TCP: $C clients =="
  run_with_optional_metrics "TCP" "tcp" "$C" 0 "$OPS" "$BENCHMARK" \
    --host "$HOST" --port "$PORT" \
    --clients "$C" --ops "$OPS" --keys "$KEYS" \
    --value-size "$VALUE_SIZE" --get-ratio "$GET_RATIO" \
    --zipf-s "$ZIPF_S" --warmup "$WARMUP" \
    --csv "$CSV_PATH"
done

cpu_sync_csv "$OUTDIR"

echo
echo "TCP experiments complete: $CSV_PATH"
