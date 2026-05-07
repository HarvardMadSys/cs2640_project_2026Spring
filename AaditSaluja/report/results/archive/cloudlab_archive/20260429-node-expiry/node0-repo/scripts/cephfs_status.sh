#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

docker compose -f docker-compose.cephfs.yml ps
echo
docker exec cs2640-ceph-mon ceph -s || true
echo
docker exec cs2640-ceph-mon ceph fs ls || true
echo
docker exec cs2640-ceph-mon ceph fs status || true
