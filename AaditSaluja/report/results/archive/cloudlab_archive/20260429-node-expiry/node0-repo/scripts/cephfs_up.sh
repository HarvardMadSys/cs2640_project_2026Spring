#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p runtime/ceph/etc runtime/ceph/lib/osd runtime/ceph/log results

docker compose -f docker-compose.cephfs.yml up -d mon
sleep 8
docker compose -f docker-compose.cephfs.yml up -d mgr
sleep 5

docker exec cs2640-ceph-mon ceph auth get client.bootstrap-osd \
  -o /var/lib/ceph/bootstrap-osd/ceph.keyring
docker exec cs2640-ceph-mon ceph auth get client.bootstrap-mds \
  -o /var/lib/ceph/bootstrap-mds/ceph.keyring

docker exec cs2640-ceph-mon ceph config set global osd_pool_default_size 1
docker exec cs2640-ceph-mon ceph config set global osd_pool_default_min_size 1
docker exec cs2640-ceph-mon ceph config set mon auth_allow_insecure_global_id_reclaim false

docker compose -f docker-compose.cephfs.yml up -d osd
sleep 10
docker compose -f docker-compose.cephfs.yml up -d mds
sleep 8

docker exec cs2640-ceph-mon ceph osd pool set cephfs_data size 1 || true
docker exec cs2640-ceph-mon ceph osd pool set cephfs_data min_size 1 || true
docker exec cs2640-ceph-mon ceph osd pool set cephfs_metadata size 1 || true
docker exec cs2640-ceph-mon ceph osd pool set cephfs_metadata min_size 1 || true

echo "CephFS containers requested. Run ./scripts/cephfs_status.sh to check health."
