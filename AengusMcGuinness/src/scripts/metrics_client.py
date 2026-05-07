#!/usr/bin/env python3
"""Client helper for scripts/metrics_collector.py."""

from __future__ import annotations

import argparse
import csv
import json
import socket
from pathlib import Path
from typing import Dict, Sequence


CPU_FIELDS = [
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

NETWORK_FIELDS = [
    "label",
    "transport",
    "clients",
    "metadata",
    "operations",
    "netdev",
    "rx_bytes_before",
    "tx_bytes_before",
    "rx_bytes_after",
    "tx_bytes_after",
    "rx_bytes_delta",
    "tx_bytes_delta",
    "total_bytes_delta",
    "rx_bytes_per_operation",
    "tx_bytes_per_operation",
    "total_bytes_per_operation",
]


def parse_endpoint(value: str, default_port: int) -> tuple[str, int]:
    if ":" not in value:
        return value, default_port
    host, port = value.rsplit(":", 1)
    return host, int(port)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send start/stop markers to metrics_collector.py.")
    parser.add_argument("--server", required=True, help="Collector host or host:port.")
    parser.add_argument("--port", type=int, default=19191, help="Collector port. Default: 19191.")
    parser.add_argument("--timeout", type=float, default=10.0, help="Socket timeout. Default: 10s.")

    subparsers = parser.add_subparsers(dest="action", required=True)

    start = subparsers.add_parser("start", help="Start one measured run.")
    start.add_argument("--run-id", required=True)
    start.add_argument("--label", required=True)
    start.add_argument("--transport", required=True)
    start.add_argument("--clients", required=True, type=int)
    start.add_argument("--metadata", required=True, type=int)
    start.add_argument("--operations", required=True, type=int)

    stop = subparsers.add_parser("stop", help="Stop one measured run and append CSV rows.")
    stop.add_argument("--run-id", required=True)
    stop.add_argument("--cpu-csv", required=True)
    stop.add_argument("--network-csv", required=True)

    subparsers.add_parser("ping", help="Check collector status.")
    subparsers.add_parser("quit", help="Ask collector to exit.")
    return parser.parse_args()


def send_request(host: str, port: int, timeout: float, payload: Dict[str, object]) -> Dict[str, object]:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(json.dumps(payload).encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)
        raw = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            raw += chunk
    response = json.loads(raw.decode("utf-8"))
    if not response.get("ok"):
        raise SystemExit(f"metrics_client: {response.get('error', 'unknown error')}")
    return response


def append_row(path: Path, fields: Sequence[str], row: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if write_header:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in fields})


def main() -> int:
    args = parse_args()
    host, port = parse_endpoint(args.server, args.port)

    if args.action == "start":
        response = send_request(
            host,
            port,
            args.timeout,
            {
                "action": "start",
                "run_id": args.run_id,
                "label": args.label,
                "transport": args.transport,
                "clients": args.clients,
                "metadata": args.metadata,
                "operations": args.operations,
            },
        )
        print(
            "metrics_client: started "
            f"run_id={response['run_id']} pid={response['pid']} netdev={response['netdev']}"
        )
        return 0

    if args.action == "stop":
        response = send_request(
            host,
            port,
            args.timeout,
            {"action": "stop", "run_id": args.run_id},
        )
        append_row(Path(args.cpu_csv), CPU_FIELDS, response["cpu"])  # type: ignore[arg-type]
        append_row(Path(args.network_csv), NETWORK_FIELDS, response["network"])  # type: ignore[arg-type]
        network = response["network"]
        print(
            "metrics_client: stopped "
            f"run_id={response['run_id']} "
            f"total_bytes_per_op={network['total_bytes_per_operation']}"
        )
        return 0

    response = send_request(host, port, args.timeout, {"action": args.action})
    print(json.dumps(response, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
