#!/usr/bin/env bash
set -euo pipefail

SCRIPT_NAME=$(basename "$0")
STATE_DIR_DEFAULT="/dev/shm/fsbench-state"
STATE_ENV_NAME="setup.env"
JOURNALD_RUNTIME_DIR="/run/systemd/journald.conf.d"
JOURNALD_RUNTIME_DROPIN="${JOURNALD_RUNTIME_DIR}/99-fsbench-volatile.conf"

usage() {
  cat <<USAGE
Usage:
  sudo ${SCRIPT_NAME} prepare [options]
  sudo ${SCRIPT_NAME} restore [--state-dir DIR]
  sudo ${SCRIPT_NAME} status  [--state-dir DIR]

prepare options:
  --ramdir DIR                 tmpfs mountpoint for in-memory logs and temp files (default: /dev/shm/fsbench-ramdir)
  --ram-size SIZE              tmpfs size for ramdir (default: 8G)
  --dump-dir DIR               Persistent directory where benchmark results are dumped later (default: /dev/shm/fsbench-results)
  --mountpoint DIR             Mountpoint used later by the run script (default: /mnt/fsbench)
  --state-dir DIR              State directory for reversible changes (default: /dev/shm/fsbench-state)
  --isolate-multi-user         Switch to multi-user.target after setup (run from a TTY, not a GUI terminal)
  --dry-run                    Print what would be done without changing the system

restore/status options:
  --state-dir DIR              State directory for reversible changes (default: /dev/shm/fsbench-state)
USAGE
}

log() { printf '[%s] %s\n' "$SCRIPT_NAME" "$*"; }
warn() { printf '[%s] WARNING: %s\n' "$SCRIPT_NAME" "$*" >&2; }
die() { printf '[%s] ERROR: %s\n' "$SCRIPT_NAME" "$*" >&2; exit 1; }
need_root() { [[ ${EUID:-$(id -u)} -eq 0 ]] || die "run as root"; }
have_cmd() { command -v "$1" >/dev/null 2>&1; }

quote_for_env() {
  local key=$1 value=$2
  printf '%s=%q\n' "$key" "$value"
}

unit_exists() {
  local unit=$1
  local state
  state=$(systemctl show -p LoadState --value "$unit" 2>/dev/null || true)
  [[ "$state" != "not-found" && -n "$state" ]]
}

record_unit_state() {
  local unit=$1 state_file=$2
  local active masked
  if ! unit_exists "$unit"; then
    return 0
  fi
  if grep -q "^${unit}"$'\t' "$state_file" 2>/dev/null; then
    return 0
  fi
  active=$(systemctl is-active "$unit" 2>/dev/null || true)
  masked=$(systemctl is-enabled "$unit" 2>/dev/null || true)
  printf '%s\t%s\t%s\n' "$unit" "$active" "$masked" >> "$state_file"
}

stop_and_mask_unit() {
  local unit=$1 state_file=$2 dry_run=$3
  if ! unit_exists "$unit"; then
    return 0
  fi
  record_unit_state "$unit" "$state_file"
  if [[ "$dry_run" == "1" ]]; then
    log "would stop and runtime-mask ${unit}"
    return 0
  fi
  systemctl stop "$unit" >/dev/null 2>&1 || true
  systemctl mask --runtime "$unit" >/dev/null 2>&1 || true
}

restore_units() {
  local state_file=$1
  [[ -f "$state_file" ]] || return 0
  while IFS=$'\t' read -r unit active masked; do
    [[ -n "$unit" ]] || continue
    if unit_exists "$unit"; then
      systemctl unmask --runtime "$unit" >/dev/null 2>&1 || true
      case "$active" in
        active|activating)
          systemctl start "$unit" >/dev/null 2>&1 || true
          ;;
      esac
    fi
  done < "$state_file"
}

mount_tmpfs() {
  local ramdir=$1 ram_size=$2 dry_run=$3
  mkdir -p "$ramdir"
  if [[ "$dry_run" == "1" ]]; then
    log "would mount tmpfs at ${ramdir} size=${ram_size}"
    return 0
  fi
  if mountpoint -q "$ramdir"; then
    log "tmpfs already mounted at ${ramdir}"
    return 0
  fi
  mount -t tmpfs -o "size=${ram_size},mode=0755,nodev,nosuid" tmpfs "$ramdir"
}

write_journald_runtime_override() {
  local dry_run=$1
  if [[ "$dry_run" == "1" ]]; then
    log "would set journald Storage=volatile via ${JOURNALD_RUNTIME_DROPIN} and restart journald"
    return 0
  fi
  mkdir -p "$JOURNALD_RUNTIME_DIR"
  cat > "$JOURNALD_RUNTIME_DROPIN" <<'JEOF'
[Journal]
Storage=volatile
RuntimeMaxUse=128M
JEOF
  systemctl daemon-reload >/dev/null 2>&1 || true
  systemctl restart systemd-journald >/dev/null 2>&1 || true
}

remove_journald_runtime_override() {
  rm -f "$JOURNALD_RUNTIME_DROPIN"
  systemctl daemon-reload >/dev/null 2>&1 || true
  systemctl restart systemd-journald >/dev/null 2>&1 || true
}

save_state_env() {
  local state_env=$1
  shift
  : > "$state_env"
  while [[ $# -gt 0 ]]; do
    local key=$1 value=$2
    quote_for_env "$key" "$value" >> "$state_env"
    shift 2
  done
}

show_status() {
  local state_dir=$1
  local state_env="${state_dir}/${STATE_ENV_NAME}"
  if [[ -f "$state_env" ]]; then
    # shellcheck disable=SC1090
    source "$state_env"
    log "state file: ${state_env}"
    printf 'device=%s\n' "${DEVICE:-}"
    printf 'partition=%s\n' "${PARTITION:-}"
    printf 'ramdir=%s\n' "${RAMDIR:-}"
    printf 'dump_dir=%s\n' "${DUMP_DIR:-}"
    printf 'mountpoint=%s\n' "${MOUNTPOINT:-}"
    printf 'isolate_multi_user=%s\n' "${ISOLATE_MULTI_USER:-0}"
  else
    log "no saved state at ${state_env}"
  fi
  if [[ -f "${state_dir}/units.tsv" ]]; then
    log "saved unit states:"
    cat "${state_dir}/units.tsv"
  fi
  if mountpoint -q /dev/shm/fsbench-ramdir 2>/dev/null; then
    log "/dev/shm/fsbench-ramdir is mounted"
  fi
}

prepare() {
  local device="/dev/nvme0n1" partition="/dev/nvme0n1p4" ramdir="/dev/shm/fsbench-ramdir" ram_size="8G"
  local dump_dir="/dev/shm/fsbench-results" mountpoint="/mnt/fsbench" state_dir="$STATE_DIR_DEFAULT"
  local isolate_multi_user=0 dry_run=0

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --ramdir) ramdir=$2; shift 2 ;;
      --ram-size) ram_size=$2; shift 2 ;;
      --dump-dir) dump_dir=$2; shift 2 ;;
      --mountpoint) mountpoint=$2; shift 2 ;;
      --state-dir) state_dir=$2; shift 2 ;;
      --isolate-multi-user) isolate_multi_user=1; shift ;;
      --dry-run) dry_run=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) die "unknown option: $1" ;;
    esac
  done

  need_root
  
  mkdir -p "$state_dir"
  : > "${state_dir}/units.tsv"

  local -a noisy_units=(
    apt-daily.timer
    apt-daily.service
    apt-daily-upgrade.timer
    apt-daily-upgrade.service
    unattended-upgrades.service
    fstrim.timer
    fstrim.service
    fwupd-refresh.timer
    fwupd-refresh.service
    fwupd.service
    logrotate.timer
    logrotate.service
    man-db.timer
    updatedb.timer
    plocate-updatedb.timer
    mlocate-updatedb.timer
    systemd-tmpfiles-clean.timer
    systemd-journal-flush.service
    packagekit.service
    packagekit-offline-update.service
    packagekit-offline-update.timer
    snapd.service
    snapd.autoimport.service
    snapd.snap-repair.timer
    snapd.seeded.service
    ua-timer.timer
    ua-timer.service
    motd-news.timer
    e2scrub_all.timer
    dpkg-db-backup.timer
    cron.service
    anacron.service
    rsyslog.service
  )

  log "stopping and runtime-masking common background writers"
  local unit
  for unit in "${noisy_units[@]}"; do
    stop_and_mask_unit "$unit" "${state_dir}/units.tsv" "$dry_run"
  done

  write_journald_runtime_override "$dry_run"
  stop_and_mask_unit systemd-journal-flush.service "${state_dir}/units.tsv" "$dry_run"

  if [[ "$dry_run" == "1" ]]; then
    log "would run swapoff -a"
  else
    swapoff -a || warn "swapoff -a failed"
  fi

  mount_tmpfs "$ramdir" "$ram_size" "$dry_run"
  mkdir -p "$dump_dir" "$mountpoint"

  if ! lsblk | grep -q nvme0n1p4; then
    die "partition not found on device"
  else
    log "using partition ${partition}"
  fi

  if [[ "$dry_run" == "0" && -n "$partition" ]]; then
    local root_src
    root_src=$(findmnt -n -o SOURCE / || true)
    if [[ "$partition" == "$root_src" ]]; then
      die "refusing to use the current root filesystem partition as the benchmark target"
    fi
  fi

  save_state_env "${state_dir}/${STATE_ENV_NAME}" \
    DEVICE "$device" \
    PARTITION "$partition" \
    RAMDIR "$ramdir" \
    RAM_SIZE "$ram_size" \
    DUMP_DIR "$dump_dir" \
    MOUNTPOINT "$mountpoint" \
    ISOLATE_MULTI_USER "$isolate_multi_user"

  log "benchmark setup state saved to ${state_dir}/${STATE_ENV_NAME}"
  log "in-memory work dir: ${ramdir}"
  log "persistent dump dir: ${dump_dir}"
  log "benchmark mountpoint: ${mountpoint}"

  if [[ "$isolate_multi_user" == "1" ]]; then
    if [[ "$dry_run" == "1" ]]; then
      log "would isolate to multi-user.target"
    else
      log "isolating to multi-user.target"
      systemctl isolate multi-user.target
    fi
  fi
}

restore() {
  local state_dir="$STATE_DIR_DEFAULT"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --state-dir) state_dir=$2; shift 2 ;;
      -h|--help) usage; exit 0 ;;
      *) die "unknown option: $1" ;;
    esac
  done
  need_root
  local state_env="${state_dir}/${STATE_ENV_NAME}"
  if [[ -f "$state_env" ]]; then
    # shellcheck disable=SC1090
    source "$state_env"
  fi
  restore_units "${state_dir}/units.tsv"
  remove_journald_runtime_override
  if [[ -n "${RAMDIR:-}" && -d "${RAMDIR:-}" ]]; then
    if mountpoint -q "${RAMDIR}"; then
      umount "${RAMDIR}" || warn "failed to unmount ${RAMDIR}"
    fi
  fi
  log "restored masked units, journald mode and tmpfs mount"
}

main() {
  for cmd in mkfs.f2fs fio cset; do
    if ! have_cmd "$cmd"; then
      die "required command not found: $cmd"
    fi
  done
  local subcmd=${1:-}
  case "$subcmd" in
    prepare)
      shift
      prepare "$@"
      ;;
    restore)
      shift
      restore "$@"
      ;;
    status)
      shift
      local state_dir="$STATE_DIR_DEFAULT"
      while [[ $# -gt 0 ]]; do
        case "$1" in
          --state-dir) state_dir=$2; shift 2 ;;
          -h|--help) usage; exit 0 ;;
          *) die "unknown option: $1" ;;
        esac
      done
      show_status "$state_dir"
      ;;
    -h|--help|"")
      usage
      ;;
    *)
      die "unknown subcommand: ${subcmd}"
      ;;
  esac
}

main "$@"
