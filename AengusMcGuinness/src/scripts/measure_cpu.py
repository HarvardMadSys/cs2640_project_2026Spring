#!/usr/bin/env python3
"""Sample Linux process CPU utilization and append a benchmark summary row.

This script is meant to run on the server node while a benchmark runs on the
client node.  It can either attach to an existing PID or launch the server
process itself:

    python3 scripts/measure_cpu.py --pid 1234 --label TCP --clients 4

    python3 scripts/measure_cpu.py --label "Two-Sided RDMA" --clients 4 -- \\
      ./build/kv_server_rdma --mode two-sided --device mlx5_3 --port 9091

CPU percentages are reported using the common "one full core == 100%" scale.
For example, a process saturating two cores reports close to 200%.
"""

from __future__ import annotations

import argparse
import csv
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Dict, List, Optional, Sequence


@dataclass
class CpuSample:
    timestamp_s: float
    process_cpu_percent: float
    host_cpu_percent: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure server CPU utilization during one benchmark run."
    )
    parser.add_argument(
        "--pid",
        type=int,
        help="Existing server process PID to sample. Omit when launching a command.",
    )
    parser.add_argument(
        "--label",
        required=True,
        help='Transport label, for example "TCP" or "One-Sided RDMA".',
    )
    parser.add_argument(
        "--transport",
        help="Machine-friendly transport name. Defaults to --label.",
    )
    parser.add_argument(
        "--clients",
        type=int,
        default=0,
        help="Client count for this benchmark row.",
    )
    parser.add_argument(
        "--metadata",
        action="store_true",
        help="Mark this run as using one-sided metadata updates.",
    )
    parser.add_argument(
        "--csv",
        default="experiments/cpu_utilization.csv",
        help="Summary CSV path to append to.",
    )
    parser.add_argument(
        "--samples-csv",
        help="Optional per-sample CSV path for debugging/noise analysis.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.20,
        help="Sampling interval in seconds.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        help="Optional fixed measurement duration in seconds.",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Optional server command to launch, written after --.",
    )
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_total_jiffies() -> Optional[int]:
    try:
        with open("/proc/stat", "r", encoding="utf-8") as handle:
            first = handle.readline().strip().split()
    except OSError:
        return None

    if not first or first[0] != "cpu":
        return None
    return sum(int(value) for value in first[1:])


def read_idle_jiffies() -> Optional[int]:
    try:
        with open("/proc/stat", "r", encoding="utf-8") as handle:
            first = handle.readline().strip().split()
    except OSError:
        return None

    if len(first) < 5 or first[0] != "cpu":
        return None

    idle = int(first[4])
    iowait = int(first[5]) if len(first) > 5 else 0
    return idle + iowait


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

    # /proc/<pid>/stat fields are 1-based.  After removing pid and comm,
    # fields[11] is utime and fields[12] is stime.
    utime = int(fields[11])
    stime = int(fields[12])
    return utime + stime


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def append_csv_row(path: Path, fieldnames: Sequence[str], row: Dict[str, object]) -> None:
    ensure_parent(path)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def start_command(command: Sequence[str]) -> subprocess.Popen[str]:
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise ValueError("empty command")
    return subprocess.Popen(  # noqa: S603 - benchmark helper runs user command intentionally
        list(command),
        text=True,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


def terminate_child(child: subprocess.Popen[str]) -> None:
    if child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=3)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=3)


def sample_process(pid: int, interval: float, duration: Optional[float], child: Optional[subprocess.Popen[str]]) -> List[CpuSample]:
    stop = False

    def handle_signal(signum: int, frame: object) -> None:  # noqa: ARG001
        nonlocal stop
        stop = True

    previous_int = signal.signal(signal.SIGINT, handle_signal)
    previous_term = signal.signal(signal.SIGTERM, handle_signal)
    previous_hup = None
    if hasattr(signal, "SIGHUP"):
        previous_hup = signal.signal(signal.SIGHUP, handle_signal)

    samples: List[CpuSample] = []
    cpu_count = os.cpu_count() or 1
    start_time = time.monotonic()

    prev_total = read_total_jiffies()
    prev_idle = read_idle_jiffies()
    prev_proc = read_process_jiffies(pid)
    if prev_total is None or prev_idle is None or prev_proc is None:
        raise RuntimeError(f"cannot read /proc CPU counters for pid {pid}")

    try:
        while not stop:
            if duration is not None and time.monotonic() - start_time >= duration:
                break
            if child is not None and child.poll() is not None:
                break

            time.sleep(interval)

            total = read_total_jiffies()
            idle = read_idle_jiffies()
            proc = read_process_jiffies(pid)
            if total is None or idle is None or proc is None:
                break

            total_delta = total - prev_total
            idle_delta = idle - prev_idle
            proc_delta = proc - prev_proc
            if total_delta > 0:
                process_cpu = max(0.0, (proc_delta / total_delta) * cpu_count * 100.0)
                host_cpu = max(0.0, ((total_delta - idle_delta) / total_delta) * 100.0)
                samples.append(CpuSample(time.time(), process_cpu, host_cpu))

            prev_total = total
            prev_idle = idle
            prev_proc = proc
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
        if previous_hup is not None and hasattr(signal, "SIGHUP"):
            signal.signal(signal.SIGHUP, previous_hup)

    return samples


def write_samples(path: Path, label: str, clients: int, metadata: bool, pid: int, samples: Sequence[CpuSample]) -> None:
    fieldnames = [
        "timestamp_s",
        "label",
        "clients",
        "metadata",
        "pid",
        "process_cpu_percent",
        "host_cpu_percent",
    ]
    ensure_parent(path)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        for sample in samples:
            writer.writerow(
                {
                    "timestamp_s": f"{sample.timestamp_s:.6f}",
                    "label": label,
                    "clients": clients,
                    "metadata": int(metadata),
                    "pid": pid,
                    "process_cpu_percent": f"{sample.process_cpu_percent:.3f}",
                    "host_cpu_percent": f"{sample.host_cpu_percent:.3f}",
                }
            )


def main() -> int:
    args = parse_args()
    if args.interval <= 0:
        raise SystemExit("--interval must be positive")
    if args.pid is None and not args.command:
        raise SystemExit("provide either --pid or a command after --")
    if args.pid is not None and args.command:
        raise SystemExit("provide either --pid or a command, not both")

    child: Optional[subprocess.Popen[str]] = None
    pid = args.pid

    try:
        if args.command:
            child = start_command(args.command)
            pid = child.pid
            # Give the server a moment to initialize before the first sample.
            time.sleep(min(args.interval, 0.25))

        assert pid is not None
        started_at = utc_now()
        started_mono = time.monotonic()
        print(
            f"measure_cpu: sampling pid={pid} label={args.label!r} "
            f"clients={args.clients} metadata={int(args.metadata)}",
            flush=True,
        )

        samples = sample_process(pid, args.interval, args.duration, child)
        ended_at = utc_now()
        duration_s = time.monotonic() - started_mono

    finally:
        if child is not None:
            terminate_child(child)

    process_values = [sample.process_cpu_percent for sample in samples]
    host_values = [sample.host_cpu_percent for sample in samples]
    avg_process = mean(process_values) if process_values else 0.0
    max_process = max(process_values) if process_values else 0.0
    avg_host = mean(host_values) if host_values else 0.0
    max_host = max(host_values) if host_values else 0.0

    summary_fields = [
        "label",
        "transport",
        "clients",
        "metadata",
        "pid",
        "started_at",
        "ended_at",
        "duration_s",
        "samples",
        "avg_process_cpu_percent",
        "max_process_cpu_percent",
        "avg_host_cpu_percent",
        "max_host_cpu_percent",
    ]
    summary_row = {
        "label": args.label,
        "transport": args.transport or args.label,
        "clients": args.clients,
        "metadata": int(args.metadata),
        "pid": pid,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_s": f"{duration_s:.3f}",
        "samples": len(samples),
        "avg_process_cpu_percent": f"{avg_process:.3f}",
        "max_process_cpu_percent": f"{max_process:.3f}",
        "avg_host_cpu_percent": f"{avg_host:.3f}",
        "max_host_cpu_percent": f"{max_host:.3f}",
    }
    append_csv_row(Path(args.csv), summary_fields, summary_row)

    if args.samples_csv:
        write_samples(Path(args.samples_csv), args.label, args.clients, args.metadata, pid, samples)

    print(
        "measure_cpu: "
        f"avg_process={avg_process:.2f}% max_process={max_process:.2f}% "
        f"avg_host={avg_host:.2f}% samples={len(samples)} "
        f"csv={args.csv}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
