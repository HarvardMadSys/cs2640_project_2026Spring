#!/usr/bin/env bash
set -euo pipefail

ARTIFACT_DIR="${1:-/tmp/cs2640-ceph-artifacts}"
FSID="${CEPH_FSID:-$(uuidgen)}"

rm -rf "$ARTIFACT_DIR"
mkdir -p "$ARTIFACT_DIR"

cat > "$ARTIFACT_DIR/ceph.conf" <<EOF_CONF
[global]
fsid = $FSID
mon initial members = node1,node2,node3
mon host = 10.10.1.2,10.10.1.3,10.10.1.4
public network = 10.10.1.0/24
cluster network = 10.10.1.0/24
auth cluster required = cephx
auth service required = cephx
auth client required = cephx
osd pool default size = 3
osd pool default min size = 2
osd crush chooseleaf type = 1
mon allow pool delete = true

[client]
keyring = /etc/ceph/ceph.client.admin.keyring
EOF_CONF

ceph-authtool --create-keyring "$ARTIFACT_DIR/ceph.mon.keyring" \
  --gen-key -n mon. --cap mon 'allow *'

ceph-authtool --create-keyring "$ARTIFACT_DIR/ceph.client.admin.keyring" \
  --gen-key -n client.admin \
  --cap mon 'allow *' \
  --cap osd 'allow *' \
  --cap mds 'allow *' \
  --cap mgr 'allow *'
ceph-authtool "$ARTIFACT_DIR/ceph.mon.keyring" \
  --import-keyring "$ARTIFACT_DIR/ceph.client.admin.keyring"

for kind in osd mds mgr; do
  keyring="$ARTIFACT_DIR/ceph.bootstrap-${kind}.keyring"
  ceph-authtool --create-keyring "$keyring" \
    --gen-key -n "client.bootstrap-${kind}" \
    --cap mon "profile bootstrap-${kind}"
  ceph-authtool "$ARTIFACT_DIR/ceph.mon.keyring" --import-keyring "$keyring"
done

monmaptool --create --fsid "$FSID" \
  --add node1 10.10.1.2 \
  --add node2 10.10.1.3 \
  --add node3 10.10.1.4 \
  "$ARTIFACT_DIR/monmap"

sudo install -m 0644 "$ARTIFACT_DIR/ceph.conf" /etc/ceph/ceph.conf
sudo install -m 0600 "$ARTIFACT_DIR/ceph.client.admin.keyring" \
  /etc/ceph/ceph.client.admin.keyring
sudo install -d -m 0755 \
  /var/lib/ceph/bootstrap-osd \
  /var/lib/ceph/bootstrap-mds \
  /var/lib/ceph/bootstrap-mgr
sudo install -m 0600 "$ARTIFACT_DIR/ceph.bootstrap-osd.keyring" \
  /var/lib/ceph/bootstrap-osd/ceph.keyring
sudo install -m 0600 "$ARTIFACT_DIR/ceph.bootstrap-mds.keyring" \
  /var/lib/ceph/bootstrap-mds/ceph.keyring
sudo install -m 0600 "$ARTIFACT_DIR/ceph.bootstrap-mgr.keyring" \
  /var/lib/ceph/bootstrap-mgr/ceph.keyring

echo "$FSID" > "$ARTIFACT_DIR/fsid"
echo "created native Ceph artifacts in $ARTIFACT_DIR"
