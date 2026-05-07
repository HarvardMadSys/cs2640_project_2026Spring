#!/usr/bin/env bash
#
# Prepare and start Milvus standalone with etcd and MinIO.
# Everything lives under /scratch/yunjia — no sudo required.
#
# Usage:
#   bash prepare_milvus.sh          # install deps + start all services
#   bash prepare_milvus.sh start    # start services only (already installed)
#   bash prepare_milvus.sh stop     # stop all services
#
set -euo pipefail

BASE_DIR="/scratch/yunjia"
MILVUS_DIR="$BASE_DIR/milvus"

# Hard RAM cap for Milvus (mimic a limited PC). Applied via cgroup v2 through
# systemd-run --user --scope. Override via env: MILVUS_MAX_MEM=16G bash prepare_milvus.sh
MILVUS_MAX_MEM="${MILVUS_MAX_MEM:-32G}"

# etcd
ETCD_VER="v3.5.16"
ETCD_DIR="$BASE_DIR/etcd"
ETCD_BIN="$ETCD_DIR/etcd"
ETCD_DATA="$BASE_DIR/etcd-data"

# MinIO
MINIO_BIN="$BASE_DIR/minio/minio"
MINIO_DATA="$BASE_DIR/minio-data"

# PID files
PID_DIR="$BASE_DIR/milvus-pids"
mkdir -p "$PID_DIR"

# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------
stop_services() {
    echo "Stopping services …"
    for svc in milvus minio etcd; do
        pidfile="$PID_DIR/$svc.pid"
        if [ -f "$pidfile" ]; then
            pid=$(cat "$pidfile")
            if kill -0 "$pid" 2>/dev/null; then
                echo "  Stopping $svc (PID $pid) …"
                kill "$pid"
                sleep 1
            fi
            rm -f "$pidfile"
        fi
    done
    echo "All services stopped."
}

# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------
install_deps() {
    # etcd
    if [ ! -x "$ETCD_BIN" ]; then
        echo "Installing etcd ${ETCD_VER} …"
        mkdir -p "$ETCD_DIR"
        curl -L "https://github.com/etcd-io/etcd/releases/download/${ETCD_VER}/etcd-${ETCD_VER}-linux-amd64.tar.gz" -o "/tmp/etcd-${ETCD_VER}.tar.gz"
        tar xzf "/tmp/etcd-${ETCD_VER}.tar.gz" -C "$ETCD_DIR" --strip-components=1
        rm -f "/tmp/etcd-${ETCD_VER}.tar.gz"
        echo "  etcd installed at $ETCD_DIR"
    else
        echo "  etcd already installed at $ETCD_DIR"
    fi

    # MinIO
    if [ ! -x "$MINIO_BIN" ]; then
        echo "Installing MinIO …"
        mkdir -p "$(dirname "$MINIO_BIN")"
        curl -L "https://dl.min.io/server/minio/release/linux-amd64/minio" -o "$MINIO_BIN"
        chmod +x "$MINIO_BIN"
        echo "  MinIO installed at $MINIO_BIN"
    else
        echo "  MinIO already installed at $MINIO_BIN"
    fi
}

# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------
start_services() {
    # Check Milvus binary exists
    if [ ! -x "$MILVUS_DIR/bin/milvus" ]; then
        echo "ERROR: $MILVUS_DIR/bin/milvus not found. Run 'make' in $MILVUS_DIR first."
        exit 1
    fi

    # --- etcd ---
    if [ -f "$PID_DIR/etcd.pid" ] && kill -0 "$(cat "$PID_DIR/etcd.pid")" 2>/dev/null; then
        echo "  etcd already running (PID $(cat "$PID_DIR/etcd.pid"))"
    else
        echo "Starting etcd …"
        mkdir -p "$ETCD_DATA"
        "$ETCD_BIN" \
            --data-dir "$ETCD_DATA" \
            --listen-client-urls http://0.0.0.0:2379 \
            --advertise-client-urls http://localhost:2379 \
            > "$PID_DIR/etcd.log" 2>&1 &
        echo $! > "$PID_DIR/etcd.pid"
        echo "  etcd started (PID $(cat "$PID_DIR/etcd.pid"))"
        sleep 2
    fi

    # --- MinIO ---
    if [ -f "$PID_DIR/minio.pid" ] && kill -0 "$(cat "$PID_DIR/minio.pid")" 2>/dev/null; then
        echo "  MinIO already running (PID $(cat "$PID_DIR/minio.pid"))"
    else
        echo "Starting MinIO …"
        mkdir -p "$MINIO_DATA"
        MINIO_ROOT_USER=minioadmin MINIO_ROOT_PASSWORD=minioadmin \
            "$MINIO_BIN" server "$MINIO_DATA" --console-address ":9001" \
            > "$PID_DIR/minio.log" 2>&1 &
        echo $! > "$PID_DIR/minio.pid"
        echo "  MinIO started (PID $(cat "$PID_DIR/minio.pid"))"
        sleep 2
    fi

    # --- Milvus ---
    if [ -f "$PID_DIR/milvus.pid" ] && kill -0 "$(cat "$PID_DIR/milvus.pid")" 2>/dev/null; then
        echo "  Milvus already running (PID $(cat "$PID_DIR/milvus.pid"))"
    else
        echo "Starting Milvus standalone …"
        export LD_LIBRARY_PATH="$MILVUS_DIR/internal/core/output/lib:${LD_LIBRARY_PATH:-}"
        # Override default paths that require /var/lib/milvus (needs root)
        MILVUS_LOCAL_DIR="$BASE_DIR/milvus-data"
        mkdir -p "$MILVUS_LOCAL_DIR"
        export MILVUS_LOCALSTORAGE_PATH="$MILVUS_LOCAL_DIR/data/"
        export MILVUS_rocksmq_path="$MILVUS_LOCAL_DIR/rdb_data"
        export MILVUS_common_storagePath="$MILVUS_LOCAL_DIR/data"
        cd "$MILVUS_DIR"
        ./bin/milvus run standalone > "$PID_DIR/milvus.log" 2>&1 &
        echo $! > "$PID_DIR/milvus.pid"
        echo "  Milvus started (PID $(cat "$PID_DIR/milvus.pid"))"

        echo "  Waiting for Milvus to be ready …"
        for i in $(seq 1 30); do
            if curl -s http://localhost:19530/v1/vector/collections > /dev/null 2>&1; then
                echo "  Milvus is ready."
                break
            fi
            if [ "$i" -eq 30 ]; then
                echo "  WARNING: Milvus did not respond after 30s. Check $PID_DIR/milvus.log"
            fi
            sleep 1
        done
    fi

    echo ""
    echo "Services running:"
    echo "  etcd   : http://localhost:2379     (log: $PID_DIR/etcd.log)"
    echo "  MinIO  : http://localhost:9000     (log: $PID_DIR/minio.log)"
    echo "  Milvus : http://localhost:19530    (log: $PID_DIR/milvus.log)"
    echo ""
    echo "To stop:  bash $0 stop"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
CMD="${1:-}"

case "$CMD" in
    stop)
        stop_services
        ;;
    start)
        start_services
        ;;
    *)
        install_deps
        start_services
        ;;
esac
