# CloudLab Reallocation Handoff

Last updated: 2026-05-06

This note records what was saved from the current CloudLab allocation before
SSH access expires and what is needed to bring a replacement allocation back up
quickly.

## Local Archive

Saved archive:

```sh
report/results/archive/cloudlab_archive/20260429-node-expiry/
```

Contents:

- `node0-results/`: complete `~/FinalProj/results` copied from node0, including
  JSON, CSV, logs, run summaries, default repeats, pin-rank diagnosis, and
  co-location diagnosis runs.
- `node0-repo/`: full node0 `~/FinalProj` tree, excluding no files during copy.
  It matches the local repo except for local-only `.venv` and
  `cloudlab_archive`.
- `node1/`, `node2/`, `node3/`: original `/tmp/cs2640-ceph-artifacts` from each
  server node.
- `node1-live-opt/`, `node2-live-opt/`, `node3-live-opt/`: live
  `/opt/cs2640-ceph/etc` and bootstrap keyrings mounted by the running Podman
  Ceph containers.
- `status/`: current node status, storage layout, `/etc/fstab`, Podman inspect
  output, shell history, Ceph client config, and `/opt/cs2640-ceph` file layout.

The full node0 result tree was also copied into local `results/`, so the repo
now has the complete experiment outputs that were previously only on node0.

## Current Allocation Snapshot

Current node map as of 2026-05-06:

- node0/client: public `pc40.cloudlab.umass.edu`, alias `cs2640` and
  `cs2640-node0`.
- node1/server: public `pc31.cloudlab.umass.edu`, alias `cs2640-node1`.
- node2/server: public `pc25.cloudlab.umass.edu`, alias `cs2640-node2`.
- node3/server: public `pc29.cloudlab.umass.edu`, alias `cs2640-node3`.

Current CephFS status from node0 on 2026-05-06:

- `HEALTH_OK`.
- Ceph version: Nautilus `14.2.22`.
- Fresh rebuild FSID: `15efc3af-565d-4f7a-80dd-4462149706d8`.
- Monitor and manager: node1.
- OSDs: 3 up, 3 in.
- MDS after rebuild: node1 active rank 0, node2 active rank 1, node3 standby.
- Pools: `cephfs_data` and `cephfs_metadata`, 48 PGs total.
- CephFS mount on node0: `/mnt/cephfs`.
- Benchmark root: `/mnt/cephfs/cs2640-bench`.

The current allocation did not expose `/dev/sdb` on the server nodes. For this
round, OSDs are directory-backed under `/opt/cs2640-ceph` on each server's root
disk. This is acceptable for within-allocation A/B comparisons because every
variant uses the same backing storage, but results should not be compared
directly against a dedicated-disk allocation without noting the hardware change.

The retained benchmark namespace under `/mnt/cephfs/cs2640-bench` is generated
test data. Result JSON/CSV/log files and Ceph pool snapshots preserve the
information needed for analysis.

## Important Compatibility Notes

- Do not use current Ceph v17/Quincy containers or Ubuntu Ceph binaries on this
  CloudLab hardware. Both failed because the CPUs lack newer x86 instruction
  support.
- Use `quay.io/ceph/daemon:latest-nautilus`.
- Run Ceph daemons with Podman containers, not `cephadm`.
- Prefer `/dev/sdb` on node1-node3 for OSD state when CloudLab provides it.
  If no extra disk exists, use directory-backed OSDs under `/opt/cs2640-ceph`
  and mark benchmark results with that caveat. Keep node0 client-only.
- The current Podman containers are not systemd-managed. Reboots require manual
  container restart unless units are added.

## Fast Rebuild Checklist

Run these after a fresh allocation with the same four-node topology.

1. Install packages.

```sh
sudo apt-get update
sudo apt-get install -y ceph-common ceph-fuse podman lvm2 xfsprogs
```

On node1, also install `ceph-mon` if `monmaptool` is absent:

```sh
command -v monmaptool || sudo apt-get install -y ceph-mon
```

2. Prepare server OSD storage on node1-node3.

```sh
sudo mkfs.xfs -f /dev/sdb
sudo mkdir -p /opt/cs2640-ceph/lib/osd
sudo mount /dev/sdb /opt/cs2640-ceph/lib/osd
sudo mkdir -p /opt/cs2640-ceph/etc /opt/cs2640-ceph/lib/bootstrap-osd /opt/cs2640-ceph/lib/bootstrap-mds
```

Add the `/dev/sdb` UUID to `/etc/fstab` on each server:

```sh
UUID=<sdb-xfs-uuid> /opt/cs2640-ceph/lib/osd xfs defaults,nofail 0 2
```

If `/dev/sdb` is absent, skip `mkfs` and mount setup and create the OSD state
directory directly:

```sh
sudo mkdir -p /opt/cs2640-ceph/lib/osd
sudo mkdir -p /opt/cs2640-ceph/etc /opt/cs2640-ceph/lib/bootstrap-osd /opt/cs2640-ceph/lib/bootstrap-mds
```

3. Generate or reuse Ceph config/keyrings.

For a fresh cluster, regenerate artifacts on node1:

```sh
./src/scripts/cloudlab_native_ceph_node1_init.sh /tmp/cs2640-ceph-artifacts
```

Then install the live container config layout on node1-node3:

```sh
sudo install -m 0644 /tmp/cs2640-ceph-artifacts/ceph.conf /opt/cs2640-ceph/etc/ceph.conf
sudo install -m 0600 /tmp/cs2640-ceph-artifacts/ceph.client.admin.keyring /opt/cs2640-ceph/etc/ceph.client.admin.keyring
sudo install -m 0600 /tmp/cs2640-ceph-artifacts/ceph.mon.keyring /opt/cs2640-ceph/etc/ceph.mon.keyring
sudo install -m 0600 /tmp/cs2640-ceph-artifacts/ceph.bootstrap-osd.keyring /opt/cs2640-ceph/lib/bootstrap-osd/ceph.keyring
sudo install -m 0600 /tmp/cs2640-ceph-artifacts/ceph.bootstrap-mds.keyring /opt/cs2640-ceph/lib/bootstrap-mds/ceph.keyring
```

If the new allocation keeps the same `node1`/`node2`/`node3` names and
`10.10.1.0/24` private network, the saved live config/keyrings under
`report/results/archive/cloudlab_archive/20260429-node-expiry/node*-live-opt/` can be used as a
reference. Prefer regenerating for a clean cluster unless continuity of FSID is
specifically required.

For the 2026-05-06 rebuild, regenerate a single-monitor config with
`mon initial members = node1` and `mon host = 10.10.1.2`. The older helper
script still documents multi-monitor artifacts, but the current working
container layout runs only one monitor on node1.

4. Start containers.

On node1:

```sh
sudo podman run -d --name cs2640-ceph-mon --net=host \
  -e MON_IP=10.10.1.2 \
  -e CEPH_PUBLIC_NETWORK=10.10.1.0/24 \
  -v /opt/cs2640-ceph/etc:/etc/ceph \
  -v /opt/cs2640-ceph/lib:/var/lib/ceph \
  quay.io/ceph/daemon:latest-nautilus mon

sudo podman run -d --name cs2640-ceph-mgr --net=host \
  -v /opt/cs2640-ceph/etc:/etc/ceph \
  -v /opt/cs2640-ceph/lib:/var/lib/ceph \
  quay.io/ceph/daemon:latest-nautilus mgr

sudo podman run -d --name cs2640-ceph-osd --net=host --privileged \
  -e OSD_TYPE=directory \
  -e OSD_FORCE_ZAP=1 \
  -v /opt/cs2640-ceph/etc:/etc/ceph \
  -v /opt/cs2640-ceph/lib:/var/lib/ceph \
  -v /dev:/dev \
  quay.io/ceph/daemon:latest-nautilus osd

sudo podman run -d --name cs2640-ceph-mds --net=host \
  -e MDS_NAME=node1 \
  -v /opt/cs2640-ceph/etc:/etc/ceph \
  -v /opt/cs2640-ceph/lib:/var/lib/ceph \
  quay.io/ceph/daemon:latest-nautilus mds
```

On node2 and node3:

```sh
sudo podman run -d --name cs2640-ceph-osd --net=host --privileged \
  -e OSD_TYPE=directory \
  -e OSD_FORCE_ZAP=1 \
  -v /opt/cs2640-ceph/etc:/etc/ceph \
  -v /opt/cs2640-ceph/lib:/var/lib/ceph \
  -v /dev:/dev \
  quay.io/ceph/daemon:latest-nautilus osd

sudo podman run -d --name cs2640-ceph-mds --net=host \
  -e MDS_NAME=$(hostname -s) \
  -v /opt/cs2640-ceph/etc:/etc/ceph \
  -v /opt/cs2640-ceph/lib:/var/lib/ceph \
  quay.io/ceph/daemon:latest-nautilus mds
```

5. Create CephFS if the daemon image did not already create it.

```sh
sudo ceph osd pool create cephfs_data 32
sudo ceph osd pool create cephfs_metadata 16
sudo ceph fs new cephfs cephfs_metadata cephfs_data
sudo ceph fs set cephfs max_mds 2
sudo ceph config set mon auth_allow_insecure_global_id_reclaim false
sudo ceph -s
sudo ceph fs status
```

6. Configure node0 as the client.

```sh
sudo install -m 0644 ceph.conf /etc/ceph/ceph.conf
sudo install -m 0640 -g cs2640 ceph.client.admin.keyring /etc/ceph/ceph.client.admin.keyring
sudo mkdir -p /mnt/cephfs
sudo ceph-fuse /mnt/cephfs
sudo mkdir -p /mnt/cephfs/cs2640-bench
sudo chown aadits:cs2640-PG0 /mnt/cephfs/cs2640-bench
```

The current working node0 client config is saved in
`report/results/archive/cloudlab_archive/20260429-node-expiry/status/node0-history-ceph-config.txt`.

## Benchmark Resume Commands

Sync the repo to node0:

```sh
rsync -az --delete ./ aadits@cs2640:~/FinalProj/
```

Smoke test:

```sh
cd ~/FinalProj
BENCH_ROOT=/mnt/cephfs/cs2640-bench \
BENCH_OUTPUT=report/results/cloudlab-smoke.json \
BENCH_CSV=report/results/cloudlab-smoke.csv \
./src/scripts/run_posix_bench.sh --suite quick
```

Current best next experiment direction:

- Placement pinning is not beating default CephFS on the current workloads.
- The small-file packing layer is the better engineering target, but the next
  version should avoid adding more CephFS files; focus on cheaper index writes
  or batched writes inside one or a very small number of physical files.
- Current final benchmark matrix:
  `report/results/cloudlab-hotcold-matrix-20260506-3x/`, with six hot/cold configs,
  native versus hybrid versus hybrid-layout, randomized serial order, and three
  repeats per cell.
- Any final benchmark matrix should randomize order and use repeated trials.

## Reallocation Checklist

When the allocation changes:

1. Update local `~/.ssh/config` so `cs2640` and the `cs2640-node*` aliases map
   to the new public hosts.
2. Refresh this handoff note and `src/docs/HOT_START.md` with the new node map.
3. Re-run `ssh -G cs2640` and one smoke benchmark before resuming larger runs.
