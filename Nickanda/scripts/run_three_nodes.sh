#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Tweak these env vars to change cluster shape.
MODE="${MODE:-leader}"              # leader | quorum
VERSIONING="${VERSIONING:-lamport}" # lamport | vector
BACKEND="${BACKEND:-memory}"        # memory | sqlite
DATA_DIR="${DATA_DIR:-}"            # required if BACKEND=sqlite
W="${W:-2}"
R="${R:-2}"
AE="${AE:-0}"                       # anti-entropy interval seconds (0 disables)

PEERS="127.0.0.1:50051,127.0.0.1:50052,127.0.0.1:50053"

extra_args() {
	local node_id="$1"
	local args=(
		--mode "$MODE"
		--versioning "$VERSIONING"
		--backend "$BACKEND"
		--w "$W"
		--r "$R"
		--anti-entropy-interval "$AE"
	)
	if [[ "$BACKEND" == "sqlite" ]]; then
		if [[ -z "$DATA_DIR" ]]; then
			echo "DATA_DIR must be set when BACKEND=sqlite" >&2
			exit 1
		fi
		args+=(--data-dir "$DATA_DIR")
	fi
	echo "${args[@]}"
}

PYTHONPATH=src python -m kvstore.node_main --node-id n1 --bind 127.0.0.1:50051 --leader 127.0.0.1:50051 --peers "$PEERS" $(extra_args n1) &
PID1=$!
PYTHONPATH=src python -m kvstore.node_main --node-id n2 --bind 127.0.0.1:50052 --leader 127.0.0.1:50051 --peers "$PEERS" $(extra_args n2) &
PID2=$!
PYTHONPATH=src python -m kvstore.node_main --node-id n3 --bind 127.0.0.1:50053 --leader 127.0.0.1:50051 --peers "$PEERS" $(extra_args n3) &
PID3=$!

cleanup() {
	kill "$PID1" "$PID2" "$PID3" 2>/dev/null || true
}

trap cleanup EXIT INT TERM
echo "3 nodes running: mode=$MODE versioning=$VERSIONING backend=$BACKEND w=$W r=$R ae=$AE"
echo "leader=127.0.0.1:50051"
wait
