#!/usr/bin/env python3
"""Server-side benchmark metrics collector.

Run this on the server node while the benchmark server is running.  Client-side
runner scripts can then send start/stop markers over a small TCP control socket
without needing SSH access from the client node to the server node.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Dict, List, Optional, Tuple


@dataclass
class CpuSample:
    process_cpu_percent: float
    host_cpu_percent: float


@dataclass
class ActiveRun:
    run_id: str
    label: str
    transport: str
    clients: int
    metadata: int
    operations: int
    pid: int
    netdev: str
    started_at: str
    start_monotonic: float
    rx_before: int
    tx_before: int
    stop_event: threading.Event = field(default_factory=threading.Event)
    samples: List[CpuSample] = field(default_factory=list)
    thread: Optional[threading.Thread] = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect server CPU and NIC bytes/op for benchmark runs.")
    parser.add_argument("--host", default="0.0.0.0", help="Control listen host. Default: 0.0.0.0.")
    parser.add_argument("--port", type=int, default=19191, help="Control listen port. Default: 19191.")
    parser.add_argument("--pid", type=int, help="Server process PID to sample.")
    parser.add_argument("--process-name", default="kv_server", help="Process name for pgrep. Default: kv_server.")
    parser.add_argument("--netdev", help="NIC interface to sample, e.g. eno1.")
    parser.add_argument("--server-ip", help="Server private-link IP used to auto-detect NIC.")
    parser.add_argument("--interval", type=float, default=0.20, help="CPU sample interval. Default: 0.20.")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_total_jiffies() -> Optional[int]:
    try:
        fields = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()
    except OSError:
        return None
    if not fields or fields[0] != "cpu":
        return None
    return sum(int(value) for value in fields[1:])


def read_idle_jiffies() -> Optional[int]:
    try:
        fields = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()
    except OSError:
        return None
    if len(fields) < 5 or fields[0] != "cpu":
        return None
    return int(fields[4]) + (int(fields[5]) if len(fields) > 5 else 0)


def read_process_jiffies(pid: int) -> Optional[int]:
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None

    close_paren = text.rfind(")")
    if close_paren < 0:
        return None
    fields = text[close_paren + 2 :].split()
    if len(fields) <= 12:
        return None
    return int(fields[11]) + int(fields[12])


def resolve_pid(pid: Optional[int], process_name: str) -> int:
    if pid:
        if read_process_jiffies(pid) is None:
            raise RuntimeError(f"cannot read /proc for pid {pid}")
        return pid

    result = subprocess.run(
        ["pgrep", "-n", "-x", process_name],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"could not find process with pgrep -n -x {process_name}")
    return int(result.stdout.strip().splitlines()[-1])


def detect_netdev(server_ip: Optional[str]) -> str:
    if not server_ip:
        raise RuntimeError("pass --netdev or --server-ip to metrics_collector.py")

    result = subprocess.run(
        ["ip", "-o", "-4", "addr", "show"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        dev = parts[1]
        ip_with_prefix = parts[3]
        ip = ip_with_prefix.split("/", 1)[0]
        if ip == server_ip:
            return dev
    raise RuntimeError(f"could not find NIC with IP {server_ip}")


def read_net_counters(netdev: str) -> Tuple[int, int]:
    base = Path("/sys/class/net") / netdev / "statistics"
    rx = int((base / "rx_bytes").read_text(encoding="utf-8").strip())
    tx = int((base / "tx_bytes").read_text(encoding="utf-8").strip())
    return rx, tx


def cpu_sample_loop(active: ActiveRun, interval: float) -> None:
    cpu_count = os.cpu_count() or 1
    prev_total = read_total_jiffies()
    prev_idle = read_idle_jiffies()
    prev_proc = read_process_jiffies(active.pid)
    if prev_total is None or prev_idle is None or prev_proc is None:
        return

    while True:
        stopped = active.stop_event.wait(interval)
        total = read_total_jiffies()
        idle = read_idle_jiffies()
        proc = read_process_jiffies(active.pid)
        if total is None or idle is None or proc is None:
            break

        total_delta = total - prev_total
        idle_delta = idle - prev_idle
        proc_delta = proc - prev_proc
        if total_delta > 0:
            active.samples.append(
                CpuSample(
                    process_cpu_percent=max(0.0, (proc_delta / total_delta) * cpu_count * 100.0),
                    host_cpu_percent=max(0.0, ((total_delta - idle_delta) / total_delta) * 100.0),
                )
            )

        prev_total = total
        prev_idle = idle
        prev_proc = proc

        if stopped:
            break


def start_run(request: Dict[str, object], args: argparse.Namespace, active: Optional[ActiveRun]) -> Tuple[Dict[str, object], Optional[ActiveRun]]:
    if active is not None:
        return {"ok": False, "error": f"run already active: {active.run_id}"}, active

    pid = resolve_pid(args.pid, args.process_name)
    netdev = args.netdev or detect_netdev(args.server_ip)
    rx_before, tx_before = read_net_counters(netdev)

    run = ActiveRun(
        run_id=str(request["run_id"]),
        label=str(request["label"]),
        transport=str(request["transport"]),
        clients=int(request["clients"]),
        metadata=int(request["metadata"]),
        operations=int(request["operations"]),
        pid=pid,
        netdev=netdev,
        started_at=utc_now(),
        start_monotonic=time.monotonic(),
        rx_before=rx_before,
        tx_before=tx_before,
    )
    run.thread = threading.Thread(target=cpu_sample_loop, args=(run, args.interval), daemon=True)
    run.thread.start()
    return {"ok": True, "run_id": run.run_id, "pid": pid, "netdev": netdev}, run


def stop_run(request: Dict[str, object], active: Optional[ActiveRun]) -> Tuple[Dict[str, object], Optional[ActiveRun]]:
    if active is None:
        return {"ok": False, "error": "no active run"}, active
    if str(request["run_id"]) != active.run_id:
        return {"ok": False, "error": f"active run is {active.run_id}, not {request['run_id']}"}, active

    active.stop_event.set()
    if active.thread is not None:
        active.thread.join(timeout=2.0)

    ended_at = utc_now()
    duration_s = time.monotonic() - active.start_monotonic
    rx_after, tx_after = read_net_counters(active.netdev)
    rx_delta = rx_after - active.rx_before
    tx_delta = tx_after - active.tx_before
    total_delta = rx_delta + tx_delta
    operations = max(0, active.operations)

    process_values = [sample.process_cpu_percent for sample in active.samples]
    host_values = [sample.host_cpu_percent for sample in active.samples]

    cpu = {
        "label": active.label,
        "transport": active.transport,
        "clients": active.clients,
        "metadata": active.metadata,
        "pid": active.pid,
        "started_at": active.started_at,
        "ended_at": ended_at,
        "duration_s": f"{duration_s:.3f}",
        "samples": len(active.samples),
        "avg_process_cpu_percent": f"{mean(process_values):.3f}" if process_values else "0.000",
        "max_process_cpu_percent": f"{max(process_values):.3f}" if process_values else "0.000",
        "avg_host_cpu_percent": f"{mean(host_values):.3f}" if host_values else "0.000",
        "max_host_cpu_percent": f"{max(host_values):.3f}" if host_values else "0.000",
    }
    network = {
        "label": active.label,
        "transport": active.transport,
        "clients": active.clients,
        "metadata": active.metadata,
        "operations": active.operations,
        "netdev": active.netdev,
        "rx_bytes_before": active.rx_before,
        "tx_bytes_before": active.tx_before,
        "rx_bytes_after": rx_after,
        "tx_bytes_after": tx_after,
        "rx_bytes_delta": rx_delta,
        "tx_bytes_delta": tx_delta,
        "total_bytes_delta": total_delta,
        "rx_bytes_per_operation": f"{(rx_delta / operations):.3f}" if operations else "0.000",
        "tx_bytes_per_operation": f"{(tx_delta / operations):.3f}" if operations else "0.000",
        "total_bytes_per_operation": f"{(total_delta / operations):.3f}" if operations else "0.000",
    }
    return {"ok": True, "run_id": active.run_id, "cpu": cpu, "network": network}, None


def handle_request(raw: bytes, args: argparse.Namespace, active: Optional[ActiveRun]) -> Tuple[Dict[str, object], Optional[ActiveRun], bool]:
    try:
        request = json.loads(raw.decode("utf-8"))
        action = request.get("action")
        if action == "start":
            response, active = start_run(request, args, active)
            return response, active, False
        if action == "stop":
            response, active = stop_run(request, active)
            return response, active, False
        if action == "ping":
            return {"ok": True, "active": active.run_id if active else None}, active, False
        if action == "quit":
            return {"ok": True}, active, True
        return {"ok": False, "error": f"unknown action: {action}"}, active, False
    except Exception as exc:  # noqa: BLE001 - control protocol should return errors
        return {"ok": False, "error": str(exc)}, active, False


def main() -> int:
    args = parse_args()
    active: Optional[ActiveRun] = None

    with socket.create_server((args.host, args.port), reuse_port=False) as server:
        print(f"metrics_collector: listening on {args.host}:{args.port}", flush=True)
        print(
            "metrics_collector: "
            f"process={args.pid or args.process_name} netdev={args.netdev or args.server_ip}",
            flush=True,
        )
        while True:
            conn, addr = server.accept()
            with conn:
                raw = conn.recv(65536)
                response, active, should_quit = handle_request(raw, args, active)
                conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
            if response.get("ok"):
                print(f"metrics_collector: {addr[0]} {response.get('run_id', '')} ok", flush=True)
            else:
                print(f"metrics_collector: error: {response.get('error')}", flush=True)
            if should_quit:
                break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
