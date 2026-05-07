#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

mkdir -p src/runtime/ceph/etc src/runtime/ceph/lib/osd src/runtime/ceph/log report/results

CEPH_MON_CONTAINER="${CEPH_MON_CONTAINER:-cs2640-ceph-mon}"
CEPH_CMD_TIMEOUT="${CEPH_CMD_TIMEOUT:-30}"

ceph_mon() {
  docker exec "$CEPH_MON_CONTAINER" timeout "$CEPH_CMD_TIMEOUT" ceph "$@"
}

show_ceph_startup_logs() {
  docker compose -f src/docker-compose.cephfs.yml ps || true
  docker logs --tail 80 "$CEPH_MON_CONTAINER" || true
}

wait_for_ceph_cli() {
  local attempts="${CEPH_CLI_ATTEMPTS:-12}"
  local delay="${CEPH_CLI_DELAY:-5}"
  local attempt
  for attempt in $(seq 1 "$attempts"); do
    if ceph_mon -s >/dev/null 2>&1; then
      return 0
    fi
    sleep "$delay"
  done
  echo "Ceph monitor CLI did not become ready after $attempts attempts." >&2
  show_ceph_startup_logs >&2
  return 1
}

docker compose -f src/docker-compose.cephfs.yml up -d mon
sleep 8
docker compose -f src/docker-compose.cephfs.yml up -d mgr
sleep 5

wait_for_ceph_cli

ceph_mon auth get client.bootstrap-osd \
  -o /var/lib/ceph/bootstrap-osd/ceph.keyring
ceph_mon auth get client.bootstrap-mds \
  -o /var/lib/ceph/bootstrap-mds/ceph.keyring

ceph_mon config set global osd_pool_default_size 1
ceph_mon config set global osd_pool_default_min_size 1
ceph_mon config set mon auth_allow_insecure_global_id_reclaim false

docker compose -f src/docker-compose.cephfs.yml up -d osd
sleep 10
docker compose -f src/docker-compose.cephfs.yml up -d mds
sleep 8

ceph_mon osd pool set cephfs_data size 1 || true
ceph_mon osd pool set cephfs_data min_size 1 || true
ceph_mon osd pool set cephfs_metadata size 1 || true
ceph_mon osd pool set cephfs_metadata min_size 1 || true

echo "CephFS containers requested. Run ./src/scripts/cephfs_status.sh to check health."
