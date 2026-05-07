#!/usr/bin/env bash
# /scratch/yunjia/milvus_experiments/wiki/experiments/monitor_mem.sh
set -euo pipefail
PIDFILE=/scratch/yunjia/milvus-pids/milvus.pid
PID=$(cat "$PIDFILE")
SCOPE=$(awk -F: '{print $3}' /proc/$PID/cgroup)
CGROUP_DIR=/sys/fs/cgroup$SCOPE

printf "%-10s %-12s %-12s %-10s %-10s %-10s %-10s\n" \
    time rss_gib vmsize_gib cg_curr cg_max ev_oom ev_high
while kill -0 "$PID" 2>/dev/null; do
    t=$(date +%H:%M:%S)
    rss_kb=$(awk '/VmRSS/ {print $2}' /proc/$PID/status)
    vm_kb=$(awk '/VmSize/ {print $2}' /proc/$PID/status)
    cg_curr=$(cat "$CGROUP_DIR/memory.current")
    cg_max=$(cat "$CGROUP_DIR/memory.max")
    ev_oom=$(awk '/^oom_kill/ {print $2}' "$CGROUP_DIR/memory.events")
    ev_high=$(awk '/^high/ {print $2}' "$CGROUP_DIR/memory.events")
    printf "%-10s %-12.2f %-12.2f %-10.2f %-10s %-10s %-10s\n" \
        "$t" \
        $(echo "scale=2; $rss_kb/1024/1024" | bc) \
        $(echo "scale=2; $vm_kb/1024/1024" | bc) \
        $(echo "scale=2; $cg_curr/1024/1024/1024" | bc) \
        "$cg_max" "$ev_oom" "$ev_high"
    sleep 1
done