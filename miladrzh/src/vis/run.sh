#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cleanup() {
  if [[ -n "${LOG_SERVER_PID:-}" ]]; then
    kill "$LOG_SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

node "$ROOT_DIR/server.js" &
LOG_SERVER_PID=$!

cd "$ROOT_DIR/app"
exec npm start
