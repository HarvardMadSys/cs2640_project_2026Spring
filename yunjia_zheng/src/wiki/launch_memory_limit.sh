#!/usr/bin/env bash
# Start or stop Milvus standalone under a user-level cgroup memory cap.
#
# Usage:
#   ./launch_memory_limit.sh start <mem>   # e.g. ./launch_memory_limit.sh start 32G
#   ./launch_memory_limit.sh stop          # stops milvus + minio + etcd
#
# <mem> is passed straight to systemd-run as MemoryMax, accepts any systemd
# size string: 32G, 16G, 8192M, etc.
set -u

BASE=/scratch/yunjia
PIDDIR=$BASE/milvus-pids
MILVUS_DIR=$BASE/milvus
MILVUS_LOCAL_DIR=$BASE/milvus-data
SCOPE_UNIT=milvus-memlimit.scope

mkdir -p "$PIDDIR"

usage() {
    echo "Usage:" >&2
    echo "  $0 start <mem>      (e.g. $0 start 32G)" >&2
    echo "  $0 stop" >&2
    exit 2
}

wait_for_gone() {
    # $1: pattern passed to pgrep -f
    for _ in $(seq 1 60); do
        pgrep -f "$1" >/dev/null || return 0
        sleep 1
    done
    return 1
}

# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------
cmd_start() {
    if [ $# -lt 1 ]; then
        echo "ERROR: missing <mem> argument" >&2
        usage
    fi
    local MEM="$1"

    # Refuse to start if something is already up. The launch line below uses
    # a relative `./bin/milvus`, so the kernel records the cmdline literally
    # as `/scratch/yunjia/milvus/./bin/milvus run standalone`. A pattern that
    # matches by `bin/milvus run standalone` catches both the relative and
    # the absolute form without depending on the inserted `./`.
    if pgrep -f "bin/milvus run standalone" >/dev/null; then
        echo "ERROR: Milvus is already running, run '$0 stop' first" >&2
        exit 1
    fi

    # Clean up any leftover transient milvus-* scope units from prior
    # launches so the new scope's MemoryMax is the only one the kernel
    # accounts against. Any residual scope keeps its old cap alive and
    # can OOM-kill processes that end up associated with it.
    local stale
    stale=$(systemctl --user list-units --type=scope --all --no-legend 2>/dev/null \
        | awk '{print $1}' | grep -E '^milvus' || true)
    for u in $stale; do
        [ -z "$u" ] && continue
        echo "[$(date +%H:%M:%S)] cleaning up leftover scope $u"
        systemctl --user stop "$u" 2>/dev/null || true
        systemctl --user reset-failed "$u" 2>/dev/null || true
    done

    # --- etcd ---
    if [ -f "$PIDDIR/etcd.pid" ] && kill -0 "$(cat "$PIDDIR/etcd.pid")" 2>/dev/null; then
        echo "[$(date +%H:%M:%S)] etcd already running (pid $(cat "$PIDDIR/etcd.pid"))"
    else
        mkdir -p "$BASE/etcd-data"
        nohup "$BASE/etcd/etcd" \
            --data-dir "$BASE/etcd-data" \
            --listen-client-urls http://0.0.0.0:2379 \
            --advertise-client-urls http://localhost:2379 \
            >"$PIDDIR/etcd.log" 2>&1 &
        echo $! >"$PIDDIR/etcd.pid"
        echo "[$(date +%H:%M:%S)] etcd started (pid $(cat "$PIDDIR/etcd.pid"))"
    fi

    # --- MinIO ---
    if [ -f "$PIDDIR/minio.pid" ] && kill -0 "$(cat "$PIDDIR/minio.pid")" 2>/dev/null; then
        echo "[$(date +%H:%M:%S)] minio already running (pid $(cat "$PIDDIR/minio.pid"))"
    else
        mkdir -p "$BASE/minio-data"
        MINIO_ROOT_USER=minioadmin MINIO_ROOT_PASSWORD=minioadmin \
            nohup "$BASE/minio/minio" server "$BASE/minio-data" --console-address ":9001" \
            >"$PIDDIR/minio.log" 2>&1 &
        echo $! >"$PIDDIR/minio.pid"
        echo "[$(date +%H:%M:%S)] minio started (pid $(cat "$PIDDIR/minio.pid"))"
    fi

    sleep 2
    ss -lntp 2>/dev/null | grep -E ':(2379|9000)' >/dev/null || {
        echo "ERROR: etcd or MinIO not listening, check $PIDDIR/*.log" >&2
        exit 1
    }

    export LD_LIBRARY_PATH="$MILVUS_DIR/internal/core/output/lib:${LD_LIBRARY_PATH:-}"
    export MILVUS_LOCALSTORAGE_PATH="$MILVUS_LOCAL_DIR/data/"
    export MILVUS_rocksmq_path="$MILVUS_LOCAL_DIR/rdb_data"
    export MILVUS_common_storagePath="$MILVUS_LOCAL_DIR/data"

    # fresh log each start so the ready-wait grep is meaningful
    : >"$PIDDIR/milvus.log"

    cd "$MILVUS_DIR"
    echo "[$(date +%H:%M:%S)] launching Milvus inside $SCOPE_UNIT with MemoryMax=$MEM"
    systemd-run --user --scope --quiet \
        --unit="$SCOPE_UNIT" \
        -p MemoryMax="$MEM" \
        -p MemorySwapMax=0 \
        ./bin/milvus run standalone \
        >"$PIDDIR/milvus.log" 2>&1 </dev/null &

    local MILVUS_SHPID=$!
    echo "$MILVUS_SHPID" >"$PIDDIR/milvus.pid"
    disown 2>/dev/null || true    # protect against SIGHUP if the caller's shell exits
    sleep 3

    echo "[$(date +%H:%M:%S)] waiting for Milvus proxy on :19530 (up to 180s) ..."
    local ready=0
    for i in $(seq 1 180); do
        # Fail fast if the Milvus process tree is gone.
        if ! pgrep -f "bin/milvus run standalone" >/dev/null; then
            echo "[$(date +%H:%M:%S)] Milvus process exited during startup after ${i}s" >&2
            echo "  Check the cgroup accounting (OOM?) and the Milvus log:" >&2
            journalctl --user -n 20 --no-pager 2>/dev/null \
                | grep -E "milvus|memory peak|oom_kill" | tail -10 >&2 || true
            echo "  --- tail $PIDDIR/milvus.log ---" >&2
            tail -20 "$PIDDIR/milvus.log" >&2
            exit 1
        fi
        if curl -s http://localhost:19530/v1/vector/collections >/dev/null 2>&1; then
            ready=1
            echo "[$(date +%H:%M:%S)] Milvus is ready after ${i}s"
            break
        fi
        sleep 1
    done
    if [ "$ready" -ne 1 ]; then
        echo "[$(date +%H:%M:%S)] Milvus did NOT become ready in 180s" >&2
        echo "  tail $PIDDIR/milvus.log:" >&2
        tail -20 "$PIDDIR/milvus.log" >&2
        exit 1
    fi

    # Verify the cgroup cap is active.
    local PID SCOPE
    PID=$(cat "$PIDDIR/milvus.pid")
    if [ -r "/proc/$PID/cgroup" ]; then
        SCOPE=$(awk -F: '{print $3}' "/proc/$PID/cgroup")
        if [ -r "/sys/fs/cgroup$SCOPE/memory.max" ]; then
            echo "[$(date +%H:%M:%S)] cgroup $SCOPE  memory.max=$(cat "/sys/fs/cgroup$SCOPE/memory.max")"
        fi
    fi

    # Release every collection that the QueryNode currently has loaded, so the
    # post-start cgroup is empty and the next per_partition_cost.py / probe
    # run loads only the variant it actually targets.
    if [ -f /home/yunjia/miniconda3/etc/profile.d/conda.sh ]; then
        # shellcheck disable=SC1091
        source /home/yunjia/miniconda3/etc/profile.d/conda.sh
        conda activate pyenv
        echo "[$(date +%H:%M:%S)] releasing every currently-loaded collection ..."
        python -u - <<'PY'
from pymilvus import MilvusClient
c = MilvusClient(uri="http://localhost:19530")
cols = c.list_collections()
print(f"collections in instance: {cols}", flush=True)
for col in cols:
    try:
        st = c.get_load_state(col)
        if "Loaded" in str(st) and "NotLoad" not in str(st):
            print(f"  release_collection({col}) ...", flush=True)
            c.release_collection(col)
            print(f"    released, new state: {c.get_load_state(col)}", flush=True)
        else:
            print(f"  skip {col} (state={st})", flush=True)
    except Exception as e:
        print(f"  {col}: {e}", flush=True)
PY
    fi

    echo "[$(date +%H:%M:%S)] done"
}

# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------
cmd_stop() {
    # Sweep every transient scope whose name starts with 'milvus'. Older
    # launches may have used a different --unit name (e.g. milvus-32g.scope),
    # and orphaned scopes keep their own cgroup alive which still has the
    # old MemoryMax value. Leaving them behind is how you end up with a
    # "32G cap" showing up in a run you launched at 80G.
    local scopes
    scopes=$(systemctl --user list-units --type=scope --all --no-legend 2>/dev/null \
        | awk '{print $1}' | grep -E '^milvus' || true)
    # reset-failed on units listed but not loaded, so --all catches them too
    scopes="$scopes $(systemctl --user list-unit-files --type=scope --no-legend 2>/dev/null \
        | awk '{print $1}' | grep -E '^milvus' || true)"
    for u in $scopes; do
        [ -z "$u" ] && continue
        echo "[$(date +%H:%M:%S)] stopping scope $u"
        systemctl --user stop "$u" 2>/dev/null || true
        systemctl --user reset-failed "$u" 2>/dev/null || true
    done

    for svc in milvus minio etcd; do
        p="$PIDDIR/$svc.pid"
        if [ -f "$p" ]; then
            pid=$(cat "$p" 2>/dev/null || true)
            if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
                echo "[$(date +%H:%M:%S)] SIGTERM $svc (pid $pid)"
                kill "$pid" 2>/dev/null || true
            fi
        fi
    done

    # Wait for each to actually exit before declaring done.
    wait_for_gone "bin/milvus run standalone" || {
        echo "[$(date +%H:%M:%S)] milvus still alive after 60s, SIGKILL"
        pkill -9 -f "bin/milvus run standalone" 2>/dev/null || true
    }
    wait_for_gone "$BASE/minio/minio" || pkill -9 -f "$BASE/minio/minio" 2>/dev/null || true
    wait_for_gone "$BASE/etcd/etcd"   || pkill -9 -f "$BASE/etcd/etcd"   2>/dev/null || true

    rm -f "$PIDDIR/milvus.pid" "$PIDDIR/minio.pid" "$PIDDIR/etcd.pid"

    echo "[$(date +%H:%M:%S)] all services stopped"
    ss -lntp 2>/dev/null | grep -E ':(19530|2379|9000)' || echo "  no listeners on :19530/:2379/:9000"
}

# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------
if [ $# -lt 1 ]; then
    usage
fi
case "$1" in
    start) shift; cmd_start "$@" ;;
    stop)  cmd_stop ;;
    *)     usage ;;
esac
