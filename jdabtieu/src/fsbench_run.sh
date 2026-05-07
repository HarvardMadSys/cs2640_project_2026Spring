#!/usr/bin/env bash
set -euo pipefail

SCRIPT_NAME=$(basename "$0")
STATE_ENV_DEFAULT="/dev/shm/fsbench-state/setup.env"

usage() {
  cat <<USAGE
Usage examples:
  sudo ${SCRIPT_NAME} --fs ext4 --mode preset   --workload seqread    --size 160G --label ext4-seqread
  sudo ${SCRIPT_NAME} --fs xfs  --mode preset   --workload randrw4k   --size 160G --runtime 120 --label xfs-randrw
  sudo ${SCRIPT_NAME} --fs btrfs --mode filebench --personality varmail --runtime 300 --fb-set nfiles=500000 --label btrfs-varmail
  sudo ${SCRIPT_NAME} --mode replay --trace /path/to/trace.bin --partition /dev/nvme0n1p4 --label replay-enterprise
  sudo ${SCRIPT_NAME} --fs ext4 --mode cmd --cmd 'python3 mytracegen.py --root "\$FSBENCH_MOUNTPOINT"' --label ext4-custom

Core options:
  --mode MODE                 preset | fiojob | replay | filebench | cmd
  --label LABEL               Run label used in filenames (default: auto-generated)
  --partition PART            Benchmark partition. Falls back to /dev/shm/fsbench-state/setup.env
  --mountpoint DIR            Mountpoint for filesystem modes (default from setup state or /mnt/fsbench)
  --ramdir DIR                tmpfs working dir for in-memory inputs/logs (default from setup state or /dev/shm/fsbench-ramdir)
  --dump-dir DIR              Persistent directory where results are dumped at the end (default from setup state or /dev/shm/fsbench-results)
  --state-env FILE            Setup-state file to source defaults from (default: /dev/shm/fsbench-state/setup.env)
  --scheduler NAME            keep | none | mq-deadline | kyber | bfq (default: none)
  --sample-sec SEC            Sampling interval for proc/sysfs metrics (default: 1)
  --capture-trace             Also capture blktrace during the measured phase (default: off)
  --copy-limit-mib N          Copy input files into tmpfs only if they are <= N MiB (default: 512)
  --cset-cpus LIST            Run the measured workload inside a cset shield on these CPUs, e.g. 3 or 2-3
  --cset-kthread on|off       Whether cset should also move kernel threads to the system set (default: on)
  --cset-sysset NAME          cset system cpuset name (default: fsbench_system)
  --cset-userset NAME         cset user cpuset name (default: fsbench_user)
  --cset-keep                 Leave any created cset shield in place after the run (default: reset on cleanup)

Filesystem preparation:
  --fs FS                     ext4 | xfs | btrfs | zfs | nilfs | f2fs. Omit for raw-device modes.
  --mount-opts OPTS          mount -o option string, e.g. noatime,compress=no
  --mkfs-extra WORDS         extra mkfs words appended after the script's defaults
  --mkfs-discard on|off      whether mkfs may discard/TRIM the whole target (default: off)
  --precondition none|device precondition raw partition before mkfs (default: device)
  --age-passes N             full-partition random-write passes for preconditioning (default: 2)

Preset fio mode:
  --workload NAME            seqread | seqwrite | randread4k | randwrite4k | randrw4k | appendfsync
  --size SIZE                working-set / file size, e.g. 160G (required for preset mode)
  --runtime SEC              time-based runtime for random/filebench/cmd workloads (default: 60)
  --bs SIZE                  block size override (default: 1M for seq*, 4k for rand*/appendfsync)
  --iodepth N                queue depth override (default: 1 for seq*/appendfsync, 32 for rand*)
  --numjobs N                worker count override (default: 1 for seq*/appendfsync, 4 for rand*)
  --rwmixread N              read percentage for randrw4k (default: 70)
  --time-based 0|1           force fio preset to use or not use time_based (default: auto)
  --cache-state keep|cold|warm   cache handling before the measured phase (default: keep)
  --ioengine NAME            fio ioengine, or auto (default: auto)
  --fio-extra WORDS          extra fio words appended after the preset defaults
  --seed N                   fio random seed (default: 1)

fiojob mode:
  --fio-job FILE             fio job file. It is copied into tmpfs if small enough.
  --fio-extra WORDS          extra fio words appended after the job file

replay mode:
  --trace FILE               iolog/blktrace file for fio --read_iolog
  --replay-no-stall 0|1      fio replay_no_stall (default: 0)
  --replay-time-scale PCT    fio replay_time_scale percentage (default: 100)
  --fio-extra WORDS          extra fio words appended after replay defaults

filebench mode:
  --personality NAME         Filebench personality, e.g. varmail, fileserver, webserver
  --runtime SEC              run time in seconds (default: 60)
  --fb-set KEY=VALUE         repeated filebench variable assignment, e.g. --fb-set nfiles=500000

cmd mode:
  --cmd STRING               shell command to run. Env vars exported: \$FSBENCH_MOUNTPOINT, \$FSBENCH_PARTITION,
                             \$FSBENCH_RAMDIR, \$FSBENCH_RUNROOT, \$FSBENCH_RUNTIME.

Notes:
  * All logs, copied inputs, manifests, and sampled telemetry live in tmpfs until the end.
  * The final archive is written only after the workload is finished and the target is quiescent.
USAGE
}

log() { printf '[%s] %s\n' "$SCRIPT_NAME" "$*"; }
warn() { printf '[%s] WARNING: %s\n' "$SCRIPT_NAME" "$*" >&2; }
die() { printf '[%s] ERROR: %s\n' "$SCRIPT_NAME" "$*" >&2; exit 1; }
need_root() { [[ ${EUID:-$(id -u)} -eq 0 ]] || die "run as root"; }
have_cmd() { command -v "$1" >/dev/null 2>&1; }
now_ms() { date +%s%3N; }

split_words() {
  local -n _out=$1
  local text=${2:-}
  _out=()
  if [[ -n "$text" ]]; then
    # shellcheck disable=SC2206
    _out=( $text )
  fi
}

load_state_defaults() {
  local state_env=$1
  if [[ -f "$state_env" ]]; then
    # shellcheck disable=SC1090
    source "$state_env"
  fi
}

sanitize_label() {
  local raw=$1
  raw=${raw// /_}
  raw=${raw//\//_}
  raw=${raw//:/_}
  raw=${raw//,/_}
  raw=${raw//[^A-Za-z0-9._-]/_}
  printf '%s\n' "$raw"
}

copy_input_to_ram_if_small() {
  local src=$1 dst_name=$2 limit_mib=$3
  [[ -f "$src" ]] || die "input file not found: ${src}"
  local size_bytes size_mib dst
  size_bytes=$(stat -c %s "$src")
  size_mib=$(( (size_bytes + 1048575) / 1048576 ))
  if (( size_mib <= limit_mib )); then
    dst="${INPUT_DIR}/${dst_name}"
    cp -f --reflink=auto "$src" "$dst"
    printf '%s\n' "$dst"
  else
    warn "keeping large input on disk: ${src} (${size_mib} MiB > ${limit_mib} MiB copy limit)"
    printf '%s\n' "$src"
  fi
}

warm_file_into_cache() {
  local path=$1
  [[ -r "$path" ]] || return 0
  dd if="$path" of=/dev/null bs=1M status=none >/dev/null 2>&1 || true
}

warm_binary_into_cache() {
  local bin=${1:-}
  [[ -n "$bin" ]] || return 0
  local path
  path=$(command -v "$bin" 2>/dev/null || true)
  [[ -n "$path" ]] || return 0
  warm_file_into_cache "$path"
  if have_cmd ldd; then
    while read -r lib; do
      [[ -n "$lib" && -r "$lib" ]] || continue
      warm_file_into_cache "$lib"
    done < <(ldd "$path" 2>/dev/null | awk '/=> \// {print $3} /^\// {print $1}')
  fi
}

get_parent_block_name() {
  local part=$1
  local pk
  pk=$(lsblk -no PKNAME "$part" 2>/dev/null | head -n1 || true)
  if [[ -n "$pk" ]]; then
    printf '%s\n' "$pk"
  else
    basename "$part"
  fi
}

wait_for_target_quiescence() {
  local dev_name=$1 timeout=${2:-60} stable_required=${3:-3}
  local statfile="/sys/class/block/${dev_name}/stat"
  [[ -r "$statfile" ]] || return 0
  local prev cur same=0 elapsed=0
  prev=$(<"$statfile")
  while (( elapsed < timeout )); do
    sleep 1
    cur=$(<"$statfile")
    if [[ "$cur" == "$prev" ]]; then
      same=$((same + 1))
      if (( same >= stable_required )); then
        return 0
      fi
    else
      prev="$cur"
      same=0
    fi
    elapsed=$((elapsed + 1))
  done
  return 1
}

set_scheduler() {
  local base_dev_name=$1 scheduler=$2
  local sched_file="/sys/block/${base_dev_name}/queue/scheduler"
  [[ "$scheduler" == "keep" ]] && return 0
  [[ -w "$sched_file" ]] || { warn "scheduler file not writable: ${sched_file}"; return 0; }
  local available
  available=$(<"$sched_file")
  if ! grep -qw "$scheduler" <<< "$available"; then
    die "scheduler ${scheduler} is not available for ${base_dev_name}; available: ${available}"
  fi
  printf '%s\n' "$scheduler" > "$sched_file"
}

require_unmounted_target() {
  local target=$1 mountpoint=${2:-}
  local current_mp
  current_mp=$(findmnt -rn -S "$target" -o TARGET 2>/dev/null || true)
  if [[ -n "$current_mp" && "$current_mp" != "$mountpoint" ]]; then
    die "${target} is mounted at ${current_mp}; unmount it before running the benchmark"
  fi
}

unmount_target_if_needed() {
  local mountpoint=$1
  if mountpoint -q "$mountpoint"; then
    sync || true
    umount "$mountpoint" || die "failed to unmount ${mountpoint}"
  fi
}

mkfs_target() {
  local fs=$1 target=$2 mkfs_discard=$3 mkfs_extra=$4
  local -a extra
  split_words extra "$mkfs_extra"
  case "$fs" in
    ext4)
      local -a cmd=(mkfs.ext4 -F -L FSBENCH)
      if [[ "$mkfs_discard" == "off" ]]; then
        cmd+=(-E nodiscard,lazy_itable_init=0)
      else
        cmd+=(-E lazy_itable_init=0)
      fi
      cmd+=("${extra[@]}" "$target")
      log "formatting ${target} as ext4"
      "${cmd[@]}" >/dev/null
      ;;
    xfs)
      local -a cmd=(mkfs.xfs -f -L FSBENCH)
      if [[ "$mkfs_discard" == "off" ]]; then
        cmd+=(-K)
      fi
      cmd+=("${extra[@]}" "$target")
      log "formatting ${target} as xfs"
      "${cmd[@]}" >/dev/null
      ;;
    btrfs)
      local -a cmd=(mkfs.btrfs -f -L FSBENCH)
      if [[ "$mkfs_discard" == "off" ]]; then
        cmd+=(-K)
      fi
      cmd+=("${extra[@]}" "$target")
      log "formatting ${target} as btrfs"
      "${cmd[@]}" >/dev/null
      ;;
    nilfs)
      if [[ -x "/usr/sbin/mkfs.nilfs2" ]]; then
        log "formatting ${target} as nilfs2"
        /usr/sbin/mkfs.nilfs2 -f "$target" >/dev/null 2>&1 || true
      else
        die "mkfs.nilfs2 not found at /usr/sbin/mkfs.nilfs2"
      fi
      ;;
    f2fs)
      if [[ -x "/usr/sbin/mkfs.f2fs" ]]; then
        local -a cmd=(/usr/sbin/mkfs.f2fs -f -l FSBENCH)
        cmd+=("${extra[@]}" "$target")
        log "formatting ${target} as f2fs"
        "${cmd[@]}" >/dev/null
      else
        die "mkfs.f2fs not found at /usr/sbin/mkfs.f2fs"
      fi
      ;;
    zfs)
      if ! have_cmd zpool; then
        die "zpool is not installed; cannot create zfs on ${target}"
      fi
      if ! have_cmd zfs; then
        die "zfs tools are not installed; cannot create zfs on ${target}"
      fi
      ZPOOL_NAME="fsbench_${RUN_ID//[^A-Za-z0-9_]/_}"
      log "creating zpool ${ZPOOL_NAME} on ${target}"
      zpool destroy -f "$ZPOOL_NAME" >/dev/null 2>&1 || true
      zpool create -f -m none "$ZPOOL_NAME" "$target" >/dev/null 2>&1
      # create a dataset mounted at the requested mountpoint
      local -a zfs_create_opts=(
        -o compression=off
        -o mountpoint="${MOUNTPOINT}"
      )
      case ",${MOUNT_OPTS}," in
        *,noatime,*)
          zfs_create_opts+=(-o atime=off -o relatime=off)
          ;;
      esac
      zfs create "${zfs_create_opts[@]}" "${ZPOOL_NAME}/FSBENCH" >/dev/null 2>&1 || true
      ZPOOL_CREATED=1
      ;;
    *)
      die "unsupported filesystem: ${fs}"
      ;;
  esac
}

mount_target_fs() {
  local fs=$1 target=$2 mountpoint=$3 mount_opts=$4
  if [[ "$fs" == "nilfs" ]]; then
    fs="nilfs2"
  fi
  if [[ "$fs" == "zfs" ]]; then
    # zfs dataset should already be created and mounted by mkfs_target
    if mountpoint -q "$mountpoint"; then
      return 0
    fi
    if have_cmd zfs && [[ -n "${ZPOOL_NAME:-}" ]]; then
      zfs set mountpoint="$mountpoint" "${ZPOOL_NAME}/FSBENCH" >/dev/null 2>&1 || true
    fi
    return 0
  fi
  mkdir -p "$mountpoint"
  if [[ -n "$mount_opts" ]]; then
    mount -t "$fs" -o "$mount_opts" "$target" "$mountpoint"
  else
    mount -t "$fs" "$target" "$mountpoint"
  fi
}

save_cmd_output() {
  local outfile=$1
  shift
  {
    printf '$ '
    printf '%q ' "$@"
    printf '\n'
    "$@"
  } > "$outfile" 2>&1 || true
}

save_text() {
  local outfile=$1
  shift
  printf '%s\n' "$*" > "$outfile"
}

run_logged_cmd() {
  local stdout_file=$1 stderr_file=$2
  shift 2
  if [[ -n "${CSET_CPUS:-}" ]]; then
    local -a cset_cmd=(cset shield --sysset="$CSET_SYSSET" --userset="$CSET_USERSET" --exec -- "$@")
    "${cset_cmd[@]}" >"$stdout_file" 2>"$stderr_file"
  else
    "$@" >"$stdout_file" 2>"$stderr_file"
  fi
}


setup_cset_shield() {
  [[ -n "${CSET_CPUS:-}" ]] || return 0
  have_cmd cset || die "--cset-cpus requested but cset is not installed"
  log "setting up cset shield on CPU(s): ${CSET_CPUS}"
  cset shield --sysset="$CSET_SYSSET" --userset="$CSET_USERSET" --cpu="$CSET_CPUS" --kthread="$CSET_KTHREAD" >/dev/null
  CSET_ACTIVE=1
}

reset_cset_shield() {
  if [[ "${CSET_ACTIVE:-0}" == "1" && "${CSET_KEEP:-0}" != "1" ]]; then
    cset shield --sysset="$CSET_SYSSET" --userset="$CSET_USERSET" --reset >/dev/null 2>&1 || true
    CSET_ACTIVE=0
  fi
}

save_cset_status() {
  local phase=$1
  [[ -n "${CSET_CPUS:-}" ]] || return 0
  if have_cmd cset; then
    save_cmd_output "${MANIFEST_DIR}/${phase}/cset-shield.txt" cset shield --sysset="$CSET_SYSSET" --userset="$CSET_USERSET"
  fi
}

snapshot_env() {
  local phase=$1
  local dir="${MANIFEST_DIR}/${phase}"
  mkdir -p "$dir"
  save_text "${dir}/timestamp.txt" "$(date -Ins --utc)"
  save_text "${dir}/cmdline.txt" "$ORIG_CMDLINE"
  save_cmd_output "${dir}/uname.txt" uname -a
  [[ -r /etc/os-release ]] && cp /etc/os-release "${dir}/os-release.txt" || true
  save_cmd_output "${dir}/lscpu.txt" lscpu
  save_cmd_output "${dir}/free.txt" free -h
  save_cmd_output "${dir}/lsblk.txt" lsblk -D -O
  save_cmd_output "${dir}/findmnt.txt" findmnt -A
  [[ -r /proc/cmdline ]] && cp /proc/cmdline "${dir}/proc-cmdline.txt" || true
  [[ -r /proc/swaps ]] && cp /proc/swaps "${dir}/proc-swaps.txt" || true
  [[ -r /proc/mounts ]] && cp /proc/mounts "${dir}/proc-mounts.txt" || true
  [[ -r "/sys/block/${BASE_DEV_NAME}/queue/scheduler" ]] && cp "/sys/block/${BASE_DEV_NAME}/queue/scheduler" "${dir}/scheduler.txt" || true
  if have_cmd sysctl; then
    save_cmd_output "${dir}/sysctl-vm.txt" sysctl \
      vm.dirty_background_bytes vm.dirty_background_ratio vm.dirty_bytes vm.dirty_ratio \
      vm.dirty_expire_centisecs vm.dirty_writeback_centisecs vm.drop_caches
  fi
  if have_cmd nvme && [[ "$BASE_DEV_NAME" == nvme* ]]; then
    save_cmd_output "${dir}/nvme-list.txt" nvme list
    save_cmd_output "${dir}/nvme-smart.txt" nvme smart-log "/dev/${BASE_DEV_NAME%p*}"
  fi
  save_cset_status "$phase"
  if [[ -n "$FS" ]]; then
    case "$FS" in
      ext4)
        if have_cmd dumpe2fs; then
          save_cmd_output "${dir}/ext4-super.txt" dumpe2fs -h "$PARTITION"
        elif have_cmd tune2fs; then
          save_cmd_output "${dir}/ext4-super.txt" tune2fs -l "$PARTITION"
        fi
        ;;
      xfs)
        if have_cmd xfs_info && mountpoint -q "$MOUNTPOINT"; then
          save_cmd_output "${dir}/xfs-info.txt" xfs_info "$MOUNTPOINT"
        fi
        ;;
      btrfs)
        if have_cmd btrfs && mountpoint -q "$MOUNTPOINT"; then
          save_cmd_output "${dir}/btrfs-show.txt" btrfs filesystem show "$MOUNTPOINT"
          save_cmd_output "${dir}/btrfs-df.txt" btrfs filesystem df "$MOUNTPOINT"
        fi
        ;;
      zfs)
        if have_cmd zfs; then
          save_cmd_output "${dir}/zfs-list.txt" zfs list
          save_cmd_output "${dir}/zpool-status.txt" zpool status || true
        fi
        ;;
    esac
  fi
}

write_phase_marker() {
  printf '%s,%s,%s\n' "$(now_ms)" "$1" "$2" >> "$MARKER_FILE"
}

start_monitor_procstat() {
  local outfile="${METRICS_DIR}/proc_stat.csv"
  echo 'ts_ms,user,nice,system,idle,iowait,irq,softirq,steal,guest,guest_nice,ctxt,processes,procs_running,procs_blocked' > "$outfile"
  (
    while true; do
      local ts
      ts=$(now_ms)
      awk -v ts="$ts" '
        /^cpu / {u=$2;n=$3;s=$4;i=$5;w=$6;irq=$7;si=$8;st=$9;g=$10;gn=$11}
        /^ctxt / {ctxt=$2}
        /^processes / {proc=$2}
        /^procs_running / {run=$2}
        /^procs_blocked / {blk=$2}
        END {
          printf "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n",
                 ts,u,n,s,i,w,irq,si,st,g,gn,ctxt,proc,run,blk
        }
      ' /proc/stat >> "$outfile"
      sleep "$SAMPLE_SEC"
    done
  ) &
  MONITOR_PIDS+=("$!")
}

start_monitor_diskstats() {
  local outfile="${METRICS_DIR}/diskstats.csv"
  echo 'ts_ms,device,reads_completed,reads_merged,sectors_read,read_ms,writes_completed,writes_merged,sectors_written,write_ms,in_flight,io_ms,weighted_io_ms,discards_completed,discards_merged,sectors_discarded,discard_ms,flushes_completed,flush_ms' > "$outfile"
  (
    while true; do
      local ts
      ts=$(now_ms)
      awk -v ts="$ts" -v base="$BASE_DEV_NAME" -v part="$PART_DEV_NAME" '
        $3==base || $3==part {
          for (i=NF+1; i<=20; i++) $i=""
          printf "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n",
                 ts,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20
        }
      ' /proc/diskstats >> "$outfile"
      sleep "$SAMPLE_SEC"
    done
  ) &
  MONITOR_PIDS+=("$!")
}

start_monitor_vmstat() {
  local outfile="${METRICS_DIR}/vmstat.csv"
  echo 'ts_ms,pgpgin,pgpgout,pswpin,pswpout,pgfault,pgmajfault,nr_dirty,nr_writeback,nr_dirtied,nr_written,workingset_refault_file,workingset_activate_file' > "$outfile"
  (
    while true; do
      local ts
      ts=$(now_ms)
      awk -v ts="$ts" '
        BEGIN {
          split("pgpgin pgpgout pswpin pswpout pgfault pgmajfault nr_dirty nr_writeback nr_dirtied nr_written workingset_refault_file workingset_activate_file", want, " ")
        }
        { vals[$1]=$2 }
        END {
          printf "%s", ts
          for (i=1; i<=12; i++) {
            k=want[i]
            printf ",%s", (k in vals ? vals[k] : "")
          }
          printf "\n"
        }
      ' /proc/vmstat >> "$outfile"
      sleep "$SAMPLE_SEC"
    done
  ) &
  MONITOR_PIDS+=("$!")
}

start_monitor_meminfo() {
  local outfile="${METRICS_DIR}/meminfo.csv"
  echo 'ts_ms,MemTotal_kB,MemFree_kB,MemAvailable_kB,Buffers_kB,Cached_kB,Dirty_kB,Writeback_kB,SwapTotal_kB,SwapFree_kB,Active_file_kB,Inactive_file_kB' > "$outfile"
  (
    while true; do
      local ts
      ts=$(now_ms)
      awk -v ts="$ts" '
        BEGIN {
          split("MemTotal MemFree MemAvailable Buffers Cached Dirty Writeback SwapTotal SwapFree Active(file) Inactive(file)", want, " ")
        }
        {
          key=$1; sub(":$", "", key)
          vals[key]=$2
        }
        END {
          printf "%s", ts
          for (i=1; i<=11; i++) {
            k=want[i]
            printf ",%s", (k in vals ? vals[k] : "")
          }
          printf "\n"
        }
      ' /proc/meminfo >> "$outfile"
      sleep "$SAMPLE_SEC"
    done
  ) &
  MONITOR_PIDS+=("$!")
}

start_monitor_pressure_file() {
  local pressure_file=$1 label=$2
  local outfile="${METRICS_DIR}/${label}.csv"
  [[ -r "$pressure_file" ]] || return 0
  echo 'ts_ms,type,avg10,avg60,avg300,total' > "$outfile"
  (
    while true; do
      local ts
      ts=$(now_ms)
      awk -v ts="$ts" '
        {
          type=$1; sub(":$", "", type)
          for (i=2; i<=NF; i++) {
            split($i, kv, "=")
            vals[kv[1]]=kv[2]
          }
          printf "%s,%s,%s,%s,%s,%s\n", ts, type, vals["avg10"], vals["avg60"], vals["avg300"], vals["total"]
          delete vals
        }
      ' "$pressure_file" >> "$outfile"
      sleep "$SAMPLE_SEC"
    done
  ) &
  MONITOR_PIDS+=("$!")
}

start_monitors() {
  MONITOR_PIDS=()
  start_monitor_procstat
  start_monitor_diskstats
  start_monitor_vmstat
  start_monitor_meminfo
  start_monitor_pressure_file /proc/pressure/io psi_io
  start_monitor_pressure_file /proc/pressure/memory psi_memory
  start_monitor_pressure_file /proc/pressure/cpu psi_cpu
}

stop_monitors() {
  local pid
  for pid in "${MONITOR_PIDS[@]:-}"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" >/dev/null 2>&1 || true
      wait "$pid" 2>/dev/null || true
    fi
  done
  MONITOR_PIDS=()
}

start_blktrace_capture() {
  BLKTRACE_PID=""
  [[ "$CAPTURE_TRACE" == "1" ]] || return 0
  if ! have_cmd blktrace; then
    warn "blktrace is not installed; skipping trace capture"
    return 0
  fi
  mkdir -p "$TRACE_DIR"
  blktrace -d "$BASE_DEVICE" -D "$TRACE_DIR" -o trace >/dev/null 2>&1 &
  BLKTRACE_PID=$!
  sleep 1
}

stop_blktrace_capture() {
  [[ -n "${BLKTRACE_PID:-}" ]] || return 0
  if kill -0 "$BLKTRACE_PID" 2>/dev/null; then
    kill -INT "$BLKTRACE_PID" >/dev/null 2>&1 || true
    wait "$BLKTRACE_PID" 2>/dev/null || true
  fi
  if have_cmd blkparse && [[ -d "$TRACE_DIR" ]]; then
    blkparse -i trace -D "$TRACE_DIR" -d "${TRACE_DIR}/trace.bin" -O >/dev/null 2>&1 || true
  fi
  BLKTRACE_PID=""
}

save_fio_common_args() {
  local log_prefix=$1 output_json=$2
  FIO_COMMON=(
    --group_reporting=1
    --eta=never
    --output-format=json+
    --output="$output_json"
    --write_bw_log="$log_prefix"
    --write_lat_log="$log_prefix"
    --write_iops_log="$log_prefix"
    --write_hist_log="$log_prefix"
    --log_avg_msec=1000
    --log_hist_msec=1000
    --percentile_list=50:90:95:99:99.9:99.99
  )
}

resolve_fio_ioengine() {
  local requested=$1
  if [[ "$requested" != "auto" ]]; then
    printf '%s\n' "$requested"
    return 0
  fi
  if fio --enghelp 2>/dev/null | grep -qw io_uring; then
    printf 'io_uring\n'
  elif fio --enghelp 2>/dev/null | grep -qw libaio; then
    printf 'libaio\n'
  else
    printf 'psync\n'
  fi
}

maybe_drop_or_warm_cache_for_file() {
  local path=$1 cache_state=$2
  case "$cache_state" in
    cold)
      sync
      echo 3 > /proc/sys/vm/drop_caches
      wait_for_target_quiescence "$PART_DEV_NAME" 60 2 || true
      ;;
    warm)
      warm_file_into_cache "$path"
      ;;
    keep)
      ;;
    *)
      die "unknown cache state: ${cache_state}"
      ;;
  esac
}

precondition_device() {
  [[ "$PRECONDITION" == "device" ]] || return 0
  local ioengine pre_dir pass
  ioengine=$(resolve_fio_ioengine "$IOENGINE")
  pre_dir="${RESULTS_DIR}/precondition"
  mkdir -p "$pre_dir"
  for pass in $(seq 1 "$AGE_PASSES"); do
    log "preconditioning pass ${pass}/${AGE_PASSES} on ${PARTITION}"
    save_fio_common_args "${pre_dir}/precond.pass${pass}" "${pre_dir}/precond.pass${pass}.json"
    fio \
      --name="precond_pass_${pass}" \
      --filename="$PARTITION" \
      --ioengine="$ioengine" \
      --direct=1 \
      --rw=randwrite \
      --bs=128k \
      --iodepth=32 \
      --numjobs=1 \
      --size=100% \
      --norandommap=1 \
      --randrepeat=1 \
      --randseed="$SEED" \
      "${FIO_COMMON[@]}"
    sync || true
    wait_for_target_quiescence "$PART_DEV_NAME" 120 3 || warn "target did not become fully quiet after preconditioning pass ${pass}"
  done
}

prepare_working_file() {
  local path=$1 size=$2 ioengine=$3
  mkdir -p "$(dirname "$path")"
  log "preparing working file"
  save_fio_common_args "${RESULTS_DIR}/prepare_file" "${RESULTS_DIR}/prepare_file.json"
  fio \
    --name=prepare_file \
    --filename="$path" \
    --ioengine="$ioengine" \
    --direct=1 \
    --rw=write \
    --bs=1M \
    --size="$size" \
    --iodepth=1 \
    --numjobs=1 \
    --fsync_on_close=1 \
    "${FIO_COMMON[@]}"
  sync || true
  wait_for_target_quiescence "$PART_DEV_NAME" 120 3 || warn "target did not become fully quiet after working-file preparation"
}

run_preset_fio() {
  log "running fio"
  [[ -n "$FS" ]] || die "preset mode requires --fs"
  [[ -n "$WORKLOAD" ]] || die "preset mode requires --workload"
  [[ -n "$SIZE" ]] || die "preset mode requires --size"
  local ioengine target_file bs iodepth numjobs time_based rw extra
  ioengine=$(resolve_fio_ioengine "$IOENGINE")
  target_file="${MOUNTPOINT}/${LABEL}.data"
  local prep_needed=0 append_mode=0 direct=1 rwmix="$RWMIXREAD"
  case "$WORKLOAD" in
    seqread)
      bs=${BS:-1M}; iodepth=${IODEPTH:-1}; numjobs=${NUMJOBS:-1}; rw=read; prep_needed=1
      ;;
    seqwrite)
      bs=${BS:-1M}; iodepth=${IODEPTH:-1}; numjobs=${NUMJOBS:-1}; rw=write
      rm -f "$target_file" || true
      ;;
    randread4k)
      bs=${BS:-4k}; iodepth=${IODEPTH:-32}; numjobs=${NUMJOBS:-4}; rw=randread; prep_needed=1
      ;;
    randwrite4k)
      bs=${BS:-4k}; iodepth=${IODEPTH:-32}; numjobs=${NUMJOBS:-4}; rw=randwrite; prep_needed=1
      ;;
    randrw4k)
      bs=${BS:-4k}; iodepth=${IODEPTH:-32}; numjobs=${NUMJOBS:-4}; rw=randrw; prep_needed=1
      ;;
    appendfsync)
      bs=${BS:-4k}; iodepth=1; numjobs=1; rw=write; append_mode=1; direct=0; ioengine=sync
      rm -f "$target_file" || true
      ;;
    *)
      die "unknown preset workload: ${WORKLOAD}"
      ;;
  esac

  if [[ "$prep_needed" == "1" ]]; then
    prepare_working_file "$target_file" "$SIZE" "$ioengine"
  fi

  maybe_drop_or_warm_cache_for_file "$target_file" "$CACHE_STATE"

  save_fio_common_args "${RESULTS_DIR}/${WORKLOAD}" "${RESULTS_DIR}/${WORKLOAD}.json"
  local -a cmd=(fio --name="$WORKLOAD" --filename="$target_file" --ioengine="$ioengine" --direct="$direct" --rw="$rw" --bs="$bs" --iodepth="$iodepth" --numjobs="$numjobs" --size="$SIZE" --randrepeat=1 --randseed="$SEED")

  case "$WORKLOAD" in
    randread4k|randwrite4k|randrw4k)
      cmd+=(--time_based=1 --runtime="$RUNTIME" --norandommap=1)
      ;;
    seqread|seqwrite)
      case "$TIME_BASED" in
        auto|0) ;;
        1) cmd+=(--time_based=1 --runtime="$RUNTIME") ;;
        *) die "--time-based must be auto, 0, or 1" ;;
      esac
      ;;
    appendfsync)
      cmd+=(--append=1 --fsync=1)
      case "$TIME_BASED" in
        auto) cmd+=(--time_based=1 --runtime="$RUNTIME") ;;
        0) ;;
        1) cmd+=(--time_based=1 --runtime="$RUNTIME") ;;
        *) die "--time-based must be auto, 0, or 1" ;;
      esac
      ;;
  esac

  if [[ "$WORKLOAD" == "randrw4k" ]]; then
    cmd+=(--rwmixread="$rwmix")
  fi

  local -a extra_words
  split_words extra_words "$FIO_EXTRA"
  cmd+=("${FIO_COMMON[@]}" "${extra_words[@]}")

  write_phase_marker benchmark start
  start_monitors
  start_blktrace_capture
  run_logged_cmd "${RESULTS_DIR}/${WORKLOAD}.stdout.txt" "${RESULTS_DIR}/${WORKLOAD}.stderr.txt" "${cmd[@]}"
  write_phase_marker benchmark stop
  write_phase_marker drain start
  sync || true
  wait_for_target_quiescence "$PART_DEV_NAME" 120 3 || warn "target did not become fully quiet after the measured phase"
  write_phase_marker drain stop
  stop_blktrace_capture
  stop_monitors
}

run_fio_jobfile() {
  [[ -n "$FIO_JOB" ]] || die "fiojob mode requires --fio-job"
  local jobfile ioengine
  jobfile=$(copy_input_to_ram_if_small "$FIO_JOB" "$(basename "$FIO_JOB")" "$COPY_LIMIT_MIB")
  warm_file_into_cache "$jobfile"
  ioengine=$(resolve_fio_ioengine "$IOENGINE")
  save_fio_common_args "${RESULTS_DIR}/fiojob" "${RESULTS_DIR}/fiojob.json"
  local -a extra_words
  split_words extra_words "$FIO_EXTRA"
  local -a cmd=(fio "$jobfile" --ioengine="$ioengine" "${FIO_COMMON[@]}")
  if [[ -n "$FS" ]]; then
    cmd+=(--directory="$MOUNTPOINT")
  fi
  cmd+=("${extra_words[@]}")
  write_phase_marker benchmark start
  start_monitors
  start_blktrace_capture
  run_logged_cmd "${RESULTS_DIR}/fiojob.stdout.txt" "${RESULTS_DIR}/fiojob.stderr.txt" "${cmd[@]}"
  write_phase_marker benchmark stop
  write_phase_marker drain start
  sync || true
  wait_for_target_quiescence "$PART_DEV_NAME" 120 3 || warn "target did not become fully quiet after the measured phase"
  write_phase_marker drain stop
  stop_blktrace_capture
  stop_monitors
}

run_replay_mode() {
  [[ -n "$TRACE_FILE" ]] || die "replay mode requires --trace"
  local trace_copy
  trace_copy=$(copy_input_to_ram_if_small "$TRACE_FILE" "$(basename "$TRACE_FILE")" "$COPY_LIMIT_MIB")
  save_fio_common_args "${RESULTS_DIR}/replay" "${RESULTS_DIR}/replay.json"
  local -a extra_words
  split_words extra_words "$FIO_EXTRA"
  local -a cmd=(
    fio
    --name=replay
    --read_iolog="$trace_copy"
    --replay_redirect="$PARTITION"
    --replay_no_stall="$REPLAY_NO_STALL"
    --replay_time_scale="$REPLAY_TIME_SCALE"
    "${FIO_COMMON[@]}"
    "${extra_words[@]}"
  )
  write_phase_marker benchmark start
  start_monitors
  start_blktrace_capture
  run_logged_cmd "${RESULTS_DIR}/replay.stdout.txt" "${RESULTS_DIR}/replay.stderr.txt" "${cmd[@]}"
  write_phase_marker benchmark stop
  write_phase_marker drain start
  sync || true
  wait_for_target_quiescence "$PART_DEV_NAME" 120 3 || warn "target did not become fully quiet after the measured phase"
  write_phase_marker drain stop
  stop_blktrace_capture
  stop_monitors
}

run_filebench_mode() {
  [[ -n "$FS" ]] || die "filebench mode requires --fs"
  [[ -n "$PERSONALITY" ]] || die "filebench mode requires --personality"
  have_cmd filebench || die "filebench is not installed"
  local fb_script="varmail.f"
  warm_binary_into_cache filebench
  write_phase_marker benchmark start
  start_monitors
  start_blktrace_capture
  run_logged_cmd "${RESULTS_DIR}/filebench.stdout.txt" "${RESULTS_DIR}/filebench.stderr.txt" env TMPDIR="$TMP_WORK_DIR" HOME="$TMP_WORK_DIR" gdb -batch -ex run --args filebench -f "$fb_script"
  write_phase_marker benchmark stop
  write_phase_marker drain start
  sync || true
  wait_for_target_quiescence "$PART_DEV_NAME" 300 3 || warn "target did not become fully quiet after the measured phase"
  write_phase_marker drain stop
  stop_blktrace_capture
  stop_monitors
}

run_cmd_mode() {
  [[ -n "$CMD_STRING" ]] || die "cmd mode requires --cmd"
  local cmd_text=$CMD_STRING
  write_phase_marker benchmark start
  start_monitors
  start_blktrace_capture
  FSBENCH_MOUNTPOINT="$MOUNTPOINT" \
  FSBENCH_PARTITION="$PARTITION" \
  FSBENCH_RAMDIR="$RAMDIR" \
  FSBENCH_RUNROOT="$RUNROOT" \
  FSBENCH_RUNTIME="$RUNTIME" \
  TMPDIR="$TMP_WORK_DIR" \
  HOME="$TMP_WORK_DIR" \
  bash -lc "$cmd_text" > "${RESULTS_DIR}/cmd.stdout.txt" 2> "${RESULTS_DIR}/cmd.stderr.txt"
  write_phase_marker benchmark stop
  write_phase_marker drain start
  sync || true
  wait_for_target_quiescence "$PART_DEV_NAME" 300 3 || warn "target did not become fully quiet after the measured phase"
  write_phase_marker drain stop
  stop_blktrace_capture
  stop_monitors
}

dump_results_archive() {
  mkdir -p "$DUMP_DIR"
  ARCHIVE_PATH="${DUMP_DIR}/${RUN_ID}.tar.gz"
  tar -C "$RUNROOT" -czf "$ARCHIVE_PATH" .
  if have_cmd sha256sum; then
    sha256sum "$ARCHIVE_PATH" > "${ARCHIVE_PATH}.sha256"
  fi
}

cleanup() {
  local ec=$?
  trap - EXIT INT TERM
  set +e
  stop_blktrace_capture
  stop_monitors
  if [[ -n "${RESULTS_DIR:-}" && -d "${RESULTS_DIR:-}" ]]; then
    if have_cmd dmesg; then
      dmesg > "${RESULTS_DIR}/dmesg.txt" 2>/dev/null || true
    fi
    if have_cmd journalctl; then
      journalctl -b > "${RESULTS_DIR}/journalctl-b.txt" 2>/dev/null || true
    fi
  fi
  if [[ -n "${MANIFEST_DIR:-}" && -d "${MANIFEST_DIR:-}" ]]; then
    snapshot_env after || true
  fi
  if [[ -n "${MOUNTPOINT:-}" && -d "${MOUNTPOINT:-}" ]]; then
    sync || true
    if mountpoint -q "$MOUNTPOINT"; then
      umount "$MOUNTPOINT" >/dev/null 2>&1 || true
    fi
  fi
  reset_cset_shield
  local dump_ok=1
  if [[ -n "${RUNROOT:-}" && -d "${RUNROOT:-}" && "${ARCHIVE_PATH:-}" == "" ]]; then
    if ! dump_results_archive; then
      warn "failed to create the final archive; leaving ${RUNROOT} intact"
      dump_ok=0
    fi
  fi
  if [[ -n "${RUNROOT:-}" && -d "${RUNROOT:-}" ]]; then
    if [[ "$dump_ok" == "1" ]]; then
      rm -rf "$RUNROOT" >/dev/null 2>&1 || true
    fi
  fi
  # If we created a ZFS pool for this run, destroy it to leave system clean
  if [[ -n "${ZPOOL_NAME:-}" && "${ZPOOL_CREATED:-0}" == "1" ]]; then
    if have_cmd zpool; then
      zpool destroy -f "$ZPOOL_NAME" >/dev/null 2>&1 || true
    fi
  fi
  exit "$ec"
}

ORIG_CMDLINE=$(printf '%q ' "$0" "$@")

MODE=""
LABEL=""
PARTITION="/dev/nvme0n1p4"
MOUNTPOINT=""
RAMDIR=""
DUMP_DIR=""
STATE_ENV="$STATE_ENV_DEFAULT"
SCHEDULER="none"
SAMPLE_SEC="1"
CAPTURE_TRACE="0"
COPY_LIMIT_MIB="512"
CSET_CPUS=""
CSET_KTHREAD="on"
CSET_SYSSET="fsbench_system"
CSET_USERSET="fsbench_user"
CSET_KEEP="0"
CSET_ACTIVE="0"
FS=""
MOUNT_OPTS=""
MKFS_EXTRA=""
MKFS_DISCARD="off"
PRECONDITION="device"
AGE_PASSES="2"
WORKLOAD=""
SIZE="160G"
RUNTIME="60"
BS=""
IODEPTH=""
NUMJOBS=""
RWMIXREAD="70"
TIME_BASED="auto"
CACHE_STATE="keep"
IOENGINE="auto"
FIO_EXTRA=""
SEED="1"
FIO_JOB=""
TRACE_FILE=""
REPLAY_NO_STALL="0"
REPLAY_TIME_SCALE="100"
PERSONALITY=""
FB_SETS=()
CMD_STRING=""

# zpool tracking for ZFS targets created during a run
ZPOOL_NAME=""
ZPOOL_CREATED=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE=$2; shift 2 ;;
    --label) LABEL=$2; shift 2 ;;
    --partition) PARTITION=$2; shift 2 ;;
    --mountpoint) MOUNTPOINT=$2; shift 2 ;;
    --ramdir) RAMDIR=$2; shift 2 ;;
    --dump-dir) DUMP_DIR=$2; shift 2 ;;
    --state-env) STATE_ENV=$2; shift 2 ;;
    --scheduler) SCHEDULER=$2; shift 2 ;;
    --sample-sec) SAMPLE_SEC=$2; shift 2 ;;
    --capture-trace) CAPTURE_TRACE="1"; shift ;;
    --copy-limit-mib) COPY_LIMIT_MIB=$2; shift 2 ;;
    --cset-cpus) CSET_CPUS=$2; shift 2 ;;
    --cset-kthread) CSET_KTHREAD=$2; shift 2 ;;
    --cset-sysset) CSET_SYSSET=$2; shift 2 ;;
    --cset-userset) CSET_USERSET=$2; shift 2 ;;
    --cset-keep) CSET_KEEP=1; shift ;;
    --fs) FS=$2; shift 2 ;;
    --mount-opts) MOUNT_OPTS=$2; shift 2 ;;
    --mkfs-extra) MKFS_EXTRA=$2; shift 2 ;;
    --mkfs-discard) MKFS_DISCARD=$2; shift 2 ;;
    --precondition) PRECONDITION=$2; shift 2 ;;
    --age-passes) AGE_PASSES=$2; shift 2 ;;
    --workload) WORKLOAD=$2; shift 2 ;;
    --size) SIZE=$2; shift 2 ;;
    --runtime) RUNTIME=$2; shift 2 ;;
    --bs) BS=$2; shift 2 ;;
    --iodepth) IODEPTH=$2; shift 2 ;;
    --numjobs) NUMJOBS=$2; shift 2 ;;
    --rwmixread) RWMIXREAD=$2; shift 2 ;;
    --time-based) TIME_BASED=$2; shift 2 ;;
    --cache-state) CACHE_STATE=$2; shift 2 ;;
    --ioengine) IOENGINE=$2; shift 2 ;;
    --fio-extra) FIO_EXTRA=$2; shift 2 ;;
    --seed) SEED=$2; shift 2 ;;
    --fio-job) FIO_JOB=$2; shift 2 ;;
    --trace) TRACE_FILE=$2; shift 2 ;;
    --replay-no-stall) REPLAY_NO_STALL=$2; shift 2 ;;
    --replay-time-scale) REPLAY_TIME_SCALE=$2; shift 2 ;;
    --personality) PERSONALITY=$2; shift 2 ;;
    --fb-set) FB_SETS+=("$2"); shift 2 ;;
    --cmd) CMD_STRING=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
 done

need_root

CLI_PARTITION=$PARTITION
CLI_MOUNTPOINT=$MOUNTPOINT
CLI_RAMDIR=$RAMDIR
CLI_DUMP_DIR=$DUMP_DIR

load_state_defaults "$STATE_ENV"

PARTITION=${CLI_PARTITION:-${PARTITION:-/dev/nvme0n1p4}}
MOUNTPOINT=${CLI_MOUNTPOINT:-${MOUNTPOINT:-/mnt/fsbench}}
RAMDIR=${CLI_RAMDIR:-${RAMDIR:-/dev/shm/fsbench-ramdir}}
DUMP_DIR=${CLI_DUMP_DIR:-${DUMP_DIR:-/dev/shm/fsbench-results}}

[[ -n "$MODE" ]] || die "--mode is required"
[[ -n "$PARTITION" ]] || die "no benchmark partition provided and no partition found in ${STATE_ENV}"
[[ -b "$PARTITION" ]] || die "benchmark partition is not a block device: ${PARTITION}"
mountpoint -q "$RAMDIR" || die "ramdir is not a mounted tmpfs: ${RAMDIR}. Run the setup script first."
[[ -d "$DUMP_DIR" ]] || mkdir -p "$DUMP_DIR"

BASE_DEV_NAME=$(get_parent_block_name "$PARTITION")
BASE_DEVICE="/dev/${BASE_DEV_NAME}"
PART_DEV_NAME=$(basename "$PARTITION")

if [[ -z "$LABEL" ]]; then
  case "$MODE" in
    preset) LABEL="${FS:-raw}-${WORKLOAD:-workload}" ;;
    fiojob) LABEL="${FS:-raw}-fiojob" ;;
    replay) LABEL="replay" ;;
    filebench) LABEL="${FS:-raw}-${PERSONALITY:-filebench}" ;;
    cmd) LABEL="${FS:-raw}-cmd" ;;
  esac
fi
LABEL=$(sanitize_label "$LABEL")
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)_${LABEL}"
RUNROOT=$(mktemp -d "${RAMDIR%/}/run.${RUN_ID}.XXXXXX")
INPUT_DIR="${RUNROOT}/inputs"
RESULTS_DIR="${RUNROOT}/results"
METRICS_DIR="${RUNROOT}/metrics"
MANIFEST_DIR="${RUNROOT}/manifest"
TRACE_DIR="${RUNROOT}/trace"
TMP_WORK_DIR="${RUNROOT}/tmp"
MARKER_FILE="${RUNROOT}/phase_markers.csv"
ARCHIVE_PATH=""
mkdir -p "$INPUT_DIR" "$RESULTS_DIR" "$METRICS_DIR" "$MANIFEST_DIR" "$TRACE_DIR" "$TMP_WORK_DIR"
echo 'ts_ms,phase,event' > "$MARKER_FILE"
trap cleanup EXIT INT TERM

warm_binary_into_cache fio
warm_binary_into_cache mount
warm_binary_into_cache umount
warm_binary_into_cache nvme
warm_binary_into_cache filebench
warm_binary_into_cache blktrace
warm_binary_into_cache blkparse

save_text "${MANIFEST_DIR}/run_id.txt" "$RUN_ID"
save_text "${MANIFEST_DIR}/mode.txt" "$MODE"
save_text "${MANIFEST_DIR}/label.txt" "$LABEL"
save_text "${MANIFEST_DIR}/partition.txt" "$PARTITION"
save_text "${MANIFEST_DIR}/base_device.txt" "$BASE_DEVICE"
save_text "${MANIFEST_DIR}/scheduler-requested.txt" "$SCHEDULER"
save_text "${MANIFEST_DIR}/fs.txt" "$FS"
save_text "${MANIFEST_DIR}/sample-sec.txt" "$SAMPLE_SEC"
save_text "${MANIFEST_DIR}/cache-state.txt" "$CACHE_STATE"
save_text "${MANIFEST_DIR}/precondition.txt" "$PRECONDITION"
save_text "${MANIFEST_DIR}/mkfs-discard.txt" "$MKFS_DISCARD"
save_text "${MANIFEST_DIR}/fio-extra.txt" "$FIO_EXTRA"
save_text "${MANIFEST_DIR}/mount-opts.txt" "$MOUNT_OPTS"
save_text "${MANIFEST_DIR}/mkfs-extra.txt" "$MKFS_EXTRA"
save_text "${MANIFEST_DIR}/orig-cmdline.txt" "$ORIG_CMDLINE"

require_unmounted_target "$PARTITION" "$MOUNTPOINT"
unmount_target_if_needed "$MOUNTPOINT"
set_scheduler "$BASE_DEV_NAME" "$SCHEDULER"
setup_cset_shield
snapshot_env before

case "$MODE" in
  replay)
    precondition_device
    run_replay_mode
    ;;
  preset|fiojob|filebench|cmd)
    if [[ -n "$FS" ]]; then
      precondition_device
      mkfs_target "$FS" "$PARTITION" "$MKFS_DISCARD" "$MKFS_EXTRA"
      mount_target_fs "$FS" "$PARTITION" "$MOUNTPOINT" "$MOUNT_OPTS"
      wait_for_target_quiescence "$PART_DEV_NAME" 60 2 || true
    fi
    case "$MODE" in
      preset) run_preset_fio ;;
      fiojob) run_fio_jobfile ;;
      filebench) run_filebench_mode ;;
      cmd) run_cmd_mode ;;
    esac
    ;;
  *)
    die "unsupported mode: ${MODE}"
    ;;
esac

log "results archive will be written to ${DUMP_DIR}/${RUN_ID}.tar.gz"
