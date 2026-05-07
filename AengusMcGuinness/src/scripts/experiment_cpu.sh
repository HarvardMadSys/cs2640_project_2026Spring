#!/usr/bin/env bash
# Shared optional CPU and NIC sampling helpers for experiment runner scripts.
#
# Runner scripts source this file and provide:
#   CPU_SSH, CPU_REMOTE_DIR, CPU_CSV, CPU_INTERVAL, SERVER_PID,
#   SERVER_PROCESS, NET_SSH, NETDEV, NET_SERVER_IP, NET_CSV, DRY_RUN, OUTDIR

cpu_enabled() {
  [[ -n "${CPU_SSH:-}" ]]
}

net_enabled() {
  [[ -n "${NET_SSH:-}" ]]
}

metrics_control_enabled() {
  [[ -n "${METRICS_CONTROL:-}" ]]
}

cpu_resolve_server_pid() {
  if [[ -n "${SERVER_PID:-}" ]]; then
    printf '%s\n' "$SERVER_PID"
    return 0
  fi

  local process_name="${SERVER_PROCESS:-}"
  [[ -n "$process_name" ]] || {
    echo "CPU sampling needs SERVER_PROCESS or --server-pid" >&2
    return 2
  }

  local quoted_process
  quoted_process="$(printf '%q' "$process_name")"
  ssh "$CPU_SSH" "pgrep -n -x $quoted_process"
}

cpu_start_sampler() {
  local label="$1"
  local transport="$2"
  local clients="$3"
  local metadata="$4"

  if ! cpu_enabled; then
    return 0
  fi

  if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
    echo "dry-run: would start server CPU sampler for label=$label clients=$clients metadata=$metadata" >&2
    return 0
  fi

  local pid
  if ! pid="$(cpu_resolve_server_pid)"; then
    echo "failed to find server PID on $CPU_SSH; pass --server-pid if pgrep is ambiguous" >&2
    return 2
  fi

  local metadata_flag=""
  if [[ "$metadata" -eq 1 ]]; then
    metadata_flag="--metadata"
  fi

  local remote_dir_expr csv_q csv_dir_q label_q transport_q interval_q log_q
  if [[ "$CPU_REMOTE_DIR" == "~" ]]; then
    remote_dir_expr='$HOME'
  elif [[ "$CPU_REMOTE_DIR" == "~/"* ]]; then
    remote_dir_expr='$HOME/'"$(printf '%q' "${CPU_REMOTE_DIR#~/}")"
  else
    remote_dir_expr="$(printf '%q' "$CPU_REMOTE_DIR")"
  fi
  csv_q="$(printf '%q' "$CPU_CSV")"
  csv_dir_q="$(printf '%q' "$(dirname "$CPU_CSV")")"
  label_q="$(printf '%q' "$label")"
  transport_q="$(printf '%q' "$transport")"
  interval_q="$(printf '%q' "$CPU_INTERVAL")"
  log_q="$(printf '%q' "/tmp/cs2640_cpu_${transport}_${clients}_${metadata}_$$.log")"

  local remote_cmd
  remote_cmd="cd $remote_dir_expr && mkdir -p $csv_dir_q && nohup python3 scripts/measure_cpu.py --pid $pid --label $label_q --transport $transport_q --clients $clients $metadata_flag --csv $csv_q --interval $interval_q > $log_q 2>&1 < /dev/null & echo \$!"

  local sampler_pid
  sampler_pid="$(ssh "$CPU_SSH" "$remote_cmd")"
  echo "CPU sampler started on $CPU_SSH: server_pid=$pid sampler_pid=$sampler_pid label=$label clients=$clients" >&2
  sleep 0.3
  printf '%s\n' "$sampler_pid"
}

cpu_stop_sampler() {
  local sampler_pid="$1"
  if ! cpu_enabled || [[ -z "$sampler_pid" ]]; then
    return 0
  fi

  if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
    echo "dry-run: would stop CPU sampler $sampler_pid on $CPU_SSH" >&2
    return 0
  fi

  local sampler_pid_q
  sampler_pid_q="$(printf '%q' "$sampler_pid")"
  ssh "$CPU_SSH" "kill -INT $sampler_pid_q 2>/dev/null || true; i=0; while kill -0 $sampler_pid_q 2>/dev/null && [ \$i -lt 50 ]; do sleep 0.1; i=\$((i + 1)); done; kill -TERM $sampler_pid_q 2>/dev/null || true"
}

net_resolve_dev() {
  if [[ -n "${NETDEV:-}" ]]; then
    printf '%s\n' "$NETDEV"
    return 0
  fi

  local server_ip="${NET_SERVER_IP:-${HOST:-}}"
  [[ -n "$server_ip" ]] || {
    echo "network sampling needs --netdev or --net-server-ip" >&2
    return 2
  }

  local server_ip_q
  server_ip_q="$(printf '%q' "$server_ip")"
  ssh "$NET_SSH" "ip -o -4 addr show | awk -v ip=$server_ip_q '{ split(\$4, a, \"/\"); if (a[1] == ip) { print \$2; exit } }'"
}

net_read_counters() {
  local netdev="$1"
  local netdev_q
  netdev_q="$(printf '%q' "$netdev")"
  ssh "$NET_SSH" "cat /sys/class/net/$netdev_q/statistics/rx_bytes; cat /sys/class/net/$netdev_q/statistics/tx_bytes"
}

net_append_row() {
  local label="$1"
  local transport="$2"
  local clients="$3"
  local metadata="$4"
  local operations="$5"
  local netdev="$6"
  local before="$7"
  local after="$8"

  local rx_before tx_before rx_after tx_after
  rx_before="$(printf '%s\n' "$before" | sed -n '1p')"
  tx_before="$(printf '%s\n' "$before" | sed -n '2p')"
  rx_after="$(printf '%s\n' "$after" | sed -n '1p')"
  tx_after="$(printf '%s\n' "$after" | sed -n '2p')"

  [[ "$rx_before" =~ ^[0-9]+$ && "$tx_before" =~ ^[0-9]+$ &&
     "$rx_after" =~ ^[0-9]+$ && "$tx_after" =~ ^[0-9]+$ ]] || {
    echo "warning: invalid NIC counter output for $netdev" >&2
    return 0
  }

  local rx_delta=$((rx_after - rx_before))
  local tx_delta=$((tx_after - tx_before))
  local total_delta=$((rx_delta + tx_delta))

  if (( rx_delta < 0 || tx_delta < 0 )); then
    echo "warning: NIC counters decreased; skipping network row for $label clients=$clients" >&2
    return 0
  fi

  local rx_per_op tx_per_op total_per_op
  rx_per_op="$(awk -v bytes="$rx_delta" -v ops="$operations" 'BEGIN { if (ops > 0) printf "%.3f", bytes / ops; else printf "0.000" }')"
  tx_per_op="$(awk -v bytes="$tx_delta" -v ops="$operations" 'BEGIN { if (ops > 0) printf "%.3f", bytes / ops; else printf "0.000" }')"
  total_per_op="$(awk -v bytes="$total_delta" -v ops="$operations" 'BEGIN { if (ops > 0) printf "%.3f", bytes / ops; else printf "0.000" }')"

  mkdir -p "$(dirname "$NET_CSV")"
  if [[ ! -s "$NET_CSV" ]]; then
    printf '%s\n' "label,transport,clients,metadata,operations,netdev,rx_bytes_before,tx_bytes_before,rx_bytes_after,tx_bytes_after,rx_bytes_delta,tx_bytes_delta,total_bytes_delta,rx_bytes_per_operation,tx_bytes_per_operation,total_bytes_per_operation" > "$NET_CSV"
  fi

  printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
    "$label" "$transport" "$clients" "$metadata" "$operations" "$netdev" \
    "$rx_before" "$tx_before" "$rx_after" "$tx_after" \
    "$rx_delta" "$tx_delta" "$total_delta" \
    "$rx_per_op" "$tx_per_op" "$total_per_op" >> "$NET_CSV"

  echo "Network bytes/op: label=$label clients=$clients netdev=$netdev total=$total_per_op rx=$rx_per_op tx=$tx_per_op"
}

run_with_optional_metrics() {
  local label="$1"
  local transport="$2"
  local clients="$3"
  local metadata="$4"
  local operations="$5"
  shift 5

  local run_id=""
  if metrics_control_enabled; then
    run_id="${transport}_${clients}_${metadata}_$(date +%s%N)_$$"
    if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
      echo "dry-run: would tell metrics collector $METRICS_CONTROL to start run_id=$run_id label=$label clients=$clients" >&2
    else
      python3 "$SCRIPT_DIR/metrics_client.py" \
        --server "$METRICS_CONTROL" \
        --port "${METRICS_PORT:-19191}" \
        start \
        --run-id "$run_id" \
        --label "$label" \
        --transport "$transport" \
        --clients "$clients" \
        --metadata "$metadata" \
        --operations "$operations"
    fi
  fi

  local sampler_pid=""
  if ! metrics_control_enabled && cpu_enabled; then
    sampler_pid="$(cpu_start_sampler "$label" "$transport" "$clients" "$metadata")"
  fi

  local netdev=""
  local net_before=""
  if ! metrics_control_enabled && net_enabled; then
    if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
      echo "dry-run: would sample server NIC counters for label=$label clients=$clients operations=$operations" >&2
    else
      netdev="$(net_resolve_dev)"
      if [[ -z "$netdev" ]]; then
        echo "failed to resolve server netdev; pass --netdev explicitly" >&2
        return 2
      fi
      net_before="$(net_read_counters "$netdev")"
    fi
  fi

  set +e
  run_cmd "$@"
  local rc=$?
  set -e

  if metrics_control_enabled; then
    if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
      echo "dry-run: would tell metrics collector $METRICS_CONTROL to stop run_id=$run_id" >&2
    else
      python3 "$SCRIPT_DIR/metrics_client.py" \
        --server "$METRICS_CONTROL" \
        --port "${METRICS_PORT:-19191}" \
        stop \
        --run-id "$run_id" \
        --cpu-csv "$CPU_CSV" \
        --network-csv "$NET_CSV"
    fi
  fi

  if ! metrics_control_enabled && net_enabled && [[ "${DRY_RUN:-0}" -eq 0 ]]; then
    local net_after
    net_after="$(net_read_counters "$netdev")"
    net_append_row "$label" "$transport" "$clients" "$metadata" "$operations" "$netdev" "$net_before" "$net_after"
  fi

  if ! metrics_control_enabled && cpu_enabled; then
    cpu_stop_sampler "$sampler_pid"
  fi

  return "$rc"
}

run_with_optional_cpu() {
  local label="$1"
  local transport="$2"
  local clients="$3"
  local metadata="$4"
  shift 4
  run_with_optional_metrics "$label" "$transport" "$clients" "$metadata" 0 "$@"
}

cpu_sync_csv() {
  local outdir="$1"
  if metrics_control_enabled; then
    return 0
  fi
  if ! cpu_enabled; then
    return 0
  fi

  mkdir -p "$outdir"
  local local_path="$outdir/$(basename "$CPU_CSV")"
  local remote_path="$CPU_SSH:$CPU_REMOTE_DIR/$CPU_CSV"

  if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
    echo "dry-run: would copy $remote_path to $local_path"
    return 0
  fi

  if scp -q "$remote_path" "$local_path"; then
    echo "CPU CSV synced to $local_path"
  else
    echo "warning: failed to sync CPU CSV from $remote_path" >&2
  fi
}
