#!/bin/bash
#
# setup_qemu_vm.sh — Launch a KVM-accelerated QEMU VM with emulated
#                     ZNS and FDP NVMe devices for benchmarking.
#
# The VM uses:
#   - KVM hardware virtualization (fast, x86-on-x86)
#   - Cloud-init for automated setup (no manual interaction)
#   - 9p virtfs to share /local/repository as /host inside the VM
#   - Two emulated NVMe devices:
#       /dev/nvme0n1 — ZNS (Zoned Namespace) device
#       /dev/nvme1n1 — FDP (Flexible Data Placement) device
#   - Backing files stored on the real NVMe at /mnt/nvme/qemu/
#
# Usage: ./setup_qemu_vm.sh [start|stop|status|ssh]
#
set -euo pipefail

NVME_MOUNT="/mnt/nvme"
QEMU_DIR="$NVME_MOUNT/qemu"
IMG_DIR="/local/build/images"
VM_DISK="$IMG_DIR/vm-disk.qcow2"
ZNS_IMG="$QEMU_DIR/zns-nvme.img"
FDP_IMG="$QEMU_DIR/fdp-nvme.img"
CONV_IMG="$QEMU_DIR/conv-nvme.img"
CLOUD_INIT_DIR="$QEMU_DIR/cloud-init"
SSH_PORT=2222
MONITOR_SOCK="$QEMU_DIR/qemu-monitor.sock"
PID_FILE="$QEMU_DIR/qemu.pid"

VM_RAM="16G"
VM_CPUS=8
ZNS_SIZE_MB=16384      # 16GB ZNS device
FDP_SIZE_MB=16384      # 16GB FDP device
CONV_SIZE_MB=16384     # 16GB conventional NVMe device
ZNS_ZONE_SIZE_MB=64    # 64MB zones

setup_images() {
    mkdir -p "$QEMU_DIR" "$CLOUD_INIT_DIR"

    # Create backing images on real NVMe
    if [ ! -f "$ZNS_IMG" ]; then
        echo "Creating ZNS backing image ($ZNS_SIZE_MB MB) ..."
        dd if=/dev/zero of="$ZNS_IMG" bs=1M count=$ZNS_SIZE_MB status=progress
    fi
    if [ ! -f "$FDP_IMG" ]; then
        echo "Creating FDP backing image ($FDP_SIZE_MB MB) ..."
        dd if=/dev/zero of="$FDP_IMG" bs=1M count=$FDP_SIZE_MB status=progress
    fi
    if [ ! -f "$CONV_IMG" ]; then
        echo "Creating conventional NVMe backing image ($CONV_SIZE_MB MB) ..."
        dd if=/dev/zero of="$CONV_IMG" bs=1M count=$CONV_SIZE_MB status=progress
    fi

    if [ ! -f ~/.ssh/id_rsa ]; then
        ssh-keygen -t rsa -N "" -f ~/.ssh/id_rsa
    fi
    PUB_KEY=$(cat ~/.ssh/id_rsa.pub)

    # Create cloud-init configuration
    cat > "$CLOUD_INIT_DIR/user-data" << EOF
#cloud-config
password: benchuser
chpasswd: { expire: False }
ssh_pwauth: True
users:
  - name: bench
    plain_text_passwd: benchuser
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    lock_passwd: false
    ssh_authorized_keys:
      - $PUB_KEY
packages:
  - build-essential
  - nvme-cli
  - fio
  - sysstat
  - liburing-dev
  - numactl
  - f2fs-tools
  - python2-minimal
  - openjdk-17-jre-headless
runcmd:
  - echo "VM setup complete" > /tmp/vm_ready
EOF

    cat > "$CLOUD_INIT_DIR/meta-data" << EOF
instance-id: nvme-bench-vm
local-hostname: nvme-bench
EOF

    # Generate cloud-init ISO
    cloud-localds "$QEMU_DIR/cloud-init.iso" \
        "$CLOUD_INIT_DIR/user-data" \
        "$CLOUD_INIT_DIR/meta-data"
}

start_vm() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "VM is already running (PID=$(cat "$PID_FILE"))"
        return 0
    fi

    setup_images

    echo "Starting QEMU VM with KVM ..."
    echo "  RAM: $VM_RAM, CPUs: $VM_CPUS"
    echo "  ZNS device: $ZNS_SIZE_MB MB, zone size: $ZNS_ZONE_SIZE_MB MB"
    echo "  FDP device: $FDP_SIZE_MB MB"
    echo "  Conventional NVMe: $CONV_SIZE_MB MB"
    echo "  SSH port forwarded: localhost:$SSH_PORT"
    echo ""

    # Calculate ZNS zone parameters
    local zone_size_sectors=$(( ZNS_ZONE_SIZE_MB * 1024 * 1024 / 512 ))
    local total_sectors=$(( ZNS_SIZE_MB * 1024 * 1024 / 512 ))
    local num_zones=$(( total_sectors / zone_size_sectors ))

    qemu-system-x86_64 \
        -enable-kvm \
        -m "$VM_RAM" \
        -smp "$VM_CPUS" \
        -cpu host \
        -display none \
        -serial file:"$QEMU_DIR/vm-serial.log" \
        -drive file="$VM_DISK",format=qcow2,if=virtio \
        -drive file="$QEMU_DIR/cloud-init.iso",format=raw,if=virtio \
        \
        -drive file="$ZNS_IMG",id=zns-drive,format=raw,if=none \
        -device nvme,serial=zns-nvme0,id=nvme0 \
        -device nvme-ns,drive=zns-drive,bus=nvme0,nsid=1,logical_block_size=4096,physical_block_size=4096,zoned=true,zoned.zone_size="$zone_size_sectors",zoned.zone_capacity="$zone_size_sectors",zoned.max_open=16,zoned.max_active=32 \
        \
        -drive file="$FDP_IMG",id=fdp-drive,format=raw,if=none \
        -device nvme,serial=fdp-nvme1,id=nvme1 \
        -device nvme-ns,drive=fdp-drive,bus=nvme1,nsid=1,logical_block_size=4096,physical_block_size=4096 \
        \
        -drive file="$CONV_IMG",id=conv-drive,format=raw,if=none \
        -device nvme,serial=conv-nvme2,id=nvme2 \
        -device nvme-ns,drive=conv-drive,bus=nvme2,nsid=1,logical_block_size=4096,physical_block_size=4096 \
        \
        -virtfs local,path=/local/repository,mount_tag=hostshare,security_model=passthrough,id=hostshare \
        \
        -net nic -net user,hostfwd=tcp::${SSH_PORT}-:22 \
        -monitor unix:"$MONITOR_SOCK",server,nowait \
        -pidfile "$PID_FILE" \
        -daemonize

    echo "VM started. Waiting for SSH to become available ..."
    for i in $(seq 1 120); do
        if ssh -q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/id_rsa -o ConnectTimeout=2 \
               -p $SSH_PORT bench@localhost echo "ready" 2>/dev/null; then
            echo "VM SSH is ready!"
            return 0
        fi
        sleep 2
    done

    echo "WARNING: VM SSH did not become ready within 4 minutes"
    return 1
}

stop_vm() {
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "Stopping VM (PID=$pid) ..."
            kill "$pid"
            sleep 3
            kill -9 "$pid" 2>/dev/null || true
        fi
        rm -f "$PID_FILE"
        echo "VM stopped."
    else
        echo "VM is not running."
    fi
}

vm_ssh() {
    ssh -q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/id_rsa -p $SSH_PORT bench@localhost "$@"
}

status_vm() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "VM is running (PID=$(cat "$PID_FILE"))"
        echo "SSH: ssh -p $SSH_PORT bench@localhost"
    else
        echo "VM is not running."
    fi
}

case "${1:-start}" in
    start)  start_vm ;;
    stop)   stop_vm ;;
    status) status_vm ;;
    ssh)    shift; vm_ssh "$@" ;;
    *)      echo "Usage: $0 {start|stop|status|ssh [command]}"; exit 1 ;;
esac
