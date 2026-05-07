#!/usr/bin/env bash
set -euo pipefail

docker exec cs2640-ceph-mon python -c '
import cephfs

fs = cephfs.LibCephFS(conffile="/etc/ceph/ceph.conf")
fs.mount()
try:
    try:
        fs.mkdir("/smoke", 0o755)
    except Exception:
        pass
    fd = fs.open("/smoke/hello.txt", "w", 0o644)
    fs.write(fd, b"cephfs-ok", 0)
    fs.close(fd)

    fd = fs.open("/smoke/hello.txt", "r", 0)
    data = fs.read(fd, 0, 64)
    fs.close(fd)

    fs.unlink("/smoke/hello.txt")
    fs.rmdir("/smoke")
    print(data.decode())
finally:
    fs.shutdown()
'

