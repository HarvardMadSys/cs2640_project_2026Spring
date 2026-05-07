#!/usr/bin/env bash
# Download experiment outputs from a CloudLab node into this local checkout.

set -euo pipefail

REMOTE=""
REMOTE_DIR="~/CS2640-Final-Project"
LOCAL_DIR="."
SKIP_PLOTS=0
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: scripts/download_cluster_results.sh --remote USER@HOST [options]

Downloads:
  REMOTE_DIR/experiments/ -> LOCAL_DIR/experiments/
  REMOTE_DIR/plots/       -> LOCAL_DIR/plots/       if present

Options:
  --remote USER@HOST      SSH target, e.g. AMcG@hp080.utah.cloudlab.us.
  --remote-dir DIR        Remote project dir. Default: ~/CS2640-Final-Project.
  --local-dir DIR         Local project dir. Default: current directory.
  --skip-plots            Download experiments only.
  --dry-run               Print rsync commands without transferring.
  -h, --help              Show this help text.
EOF
}

die() {
  echo "download_cluster_results: $*" >&2
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
    --remote) REMOTE="${2:-}"; shift 2 ;;
    --remote-dir) REMOTE_DIR="${2:-}"; shift 2 ;;
    --local-dir) LOCAL_DIR="${2:-}"; shift 2 ;;
    --skip-plots) SKIP_PLOTS=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ -n "$REMOTE" ]] || die "pass --remote USER@HOST"

mkdir -p "$LOCAL_DIR/experiments"
run_cmd rsync -az "$REMOTE:$REMOTE_DIR/experiments/" "$LOCAL_DIR/experiments/"

if [[ "$SKIP_PLOTS" -eq 0 ]]; then
  mkdir -p "$LOCAL_DIR/plots"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "dry-run: would download plots if $REMOTE:$REMOTE_DIR/plots exists"
    print_cmd rsync -az "$REMOTE:$REMOTE_DIR/plots/" "$LOCAL_DIR/plots/"
  elif ssh "$REMOTE" "test -d $REMOTE_DIR/plots"; then
    run_cmd rsync -az "$REMOTE:$REMOTE_DIR/plots/" "$LOCAL_DIR/plots/"
  else
    echo "skip: remote plots directory does not exist; generate plots locally with scripts/plot_all_results.sh"
  fi
fi

echo "Download complete."
