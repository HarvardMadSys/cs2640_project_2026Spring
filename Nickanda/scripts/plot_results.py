#!/usr/bin/env python3
"""Generate PNG plots from the CSVs produced by ``scripts/run_experiments.py``.

Outputs go to ``docs/plots/``. Uses only matplotlib (no seaborn).
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
PLOTS = DOCS / "plots"


def _read_aggregate() -> list[dict]:
	rows: list[dict] = []
	path = DOCS / "experiment_results.csv"
	if not path.exists():
		return rows
	with path.open() as f:
		for row in csv.DictReader(f):
			for key in (
				"throughput_ops_sec",
				"p50_ms",
				"p95_ms",
				"p99_ms",
				"p999_ms",
				"availability",
			):
				row[key] = float(row[key])
			for key in (
				"ops",
				"completed",
				"errors",
				"repair_ops_total",
				"repair_bytes_total",
				"read_repair_ops_total",
				"anti_entropy_rounds_total",
			):
				row[key] = int(row[key])
			rows.append(row)
	return rows


def _read_latencies() -> dict[tuple[str, str, str], list[float]]:
	out: dict[tuple[str, str, str], list[float]] = defaultdict(list)
	path = DOCS / "experiment_latencies.csv"
	if not path.exists():
		return out
	with path.open() as f:
		for row in csv.DictReader(f):
			out[(row["mode"], row["scenario"], row["workload"])].append(
				float(row["latency_ms"])
			)
	return out


def _read_availability() -> dict[tuple[str, str, str], list[tuple[float, float]]]:
	out: dict[tuple[str, str, str], list[tuple[float, float]]] = defaultdict(list)
	path = DOCS / "experiment_availability.csv"
	if not path.exists():
		return out
	with path.open() as f:
		for row in csv.DictReader(f):
			out[(row["mode"], row["scenario"], row["workload"])].append(
				(float(row["elapsed_sec"]), float(row["window_availability"]))
			)
	return out


def _save(fig, name: str) -> None:
	PLOTS.mkdir(parents=True, exist_ok=True)
	path = PLOTS / name
	fig.tight_layout()
	fig.savefig(path, dpi=140)
	plt.close(fig)
	print(f"wrote {path}")


def plot_throughput_per_workload(rows: list[dict]) -> None:
	by_workload: dict[str, list[dict]] = defaultdict(list)
	for r in rows:
		by_workload[r["workload"]].append(r)

	for workload, items in by_workload.items():
		scenarios = sorted({r["scenario"] for r in items})
		modes = sorted({r["mode"] for r in items})
		fig, ax = plt.subplots(figsize=(10, 5))
		width = 0.8 / max(1, len(modes))
		x = list(range(len(scenarios)))
		for i, mode in enumerate(modes):
			vals = []
			for sc in scenarios:
				match = [r for r in items if r["mode"] == mode and r["scenario"] == sc]
				vals.append(match[0]["throughput_ops_sec"] if match else 0.0)
			ax.bar(
				[xi + i * width for xi in x],
				vals,
				width=width,
				label=mode,
			)
		ax.set_xticks([xi + (len(modes) - 1) * width / 2 for xi in x])
		ax.set_xticklabels(scenarios, rotation=25, ha="right")
		ax.set_ylabel("Throughput (ops/sec)")
		ax.set_title(f"Throughput - {workload}")
		ax.legend(fontsize=8)
		_save(fig, f"throughput__{workload}.png")


def plot_latency_percentiles(rows: list[dict]) -> None:
	by_workload: dict[str, list[dict]] = defaultdict(list)
	for r in rows:
		by_workload[r["workload"]].append(r)

	for workload, items in by_workload.items():
		scenarios = sorted({r["scenario"] for r in items})
		modes = sorted({r["mode"] for r in items})
		for pct_key, label in (("p50_ms", "p50"), ("p95_ms", "p95"), ("p99_ms", "p99")):
			fig, ax = plt.subplots(figsize=(10, 5))
			width = 0.8 / max(1, len(modes))
			x = list(range(len(scenarios)))
			for i, mode in enumerate(modes):
				vals = []
				for sc in scenarios:
					match = [r for r in items if r["mode"] == mode and r["scenario"] == sc]
					vals.append(match[0][pct_key] if match else 0.0)
				ax.bar(
					[xi + i * width for xi in x],
					vals,
					width=width,
					label=mode,
				)
			ax.set_xticks([xi + (len(modes) - 1) * width / 2 for xi in x])
			ax.set_xticklabels(scenarios, rotation=25, ha="right")
			ax.set_ylabel(f"Latency {label} (ms)")
			ax.set_title(f"Latency {label} - {workload}")
			ax.legend(fontsize=8)
			_save(fig, f"latency__{workload}__{label}.png")


def plot_cdfs(lat: dict[tuple[str, str, str], list[float]]) -> None:
	by_ws: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(dict)
	for (mode, scenario, workload), vals in lat.items():
		by_ws[(workload, scenario)][mode] = vals

	for (workload, scenario), mode_vals in by_ws.items():
		if not any(mode_vals.values()):
			continue
		fig, ax = plt.subplots(figsize=(8, 5))
		for mode, vals in sorted(mode_vals.items()):
			if not vals:
				continue
			ordered = sorted(vals)
			ys = [(i + 1) / len(ordered) for i in range(len(ordered))]
			ax.plot(ordered, ys, label=mode)
		ax.set_xlabel("Latency (ms)")
		ax.set_ylabel("CDF")
		ax.set_title(f"Latency CDF - {workload} / {scenario}")
		ax.grid(True, alpha=0.3)
		ax.legend(fontsize=8)
		_save(fig, f"cdf__{workload}__{scenario}.png")


def plot_repair_overhead(rows: list[dict]) -> None:
	subset = [r for r in rows if r["repair_ops_total"] > 0 or r["anti_entropy_rounds_total"] > 0]
	if not subset:
		return
	by_scenario: dict[str, list[dict]] = defaultdict(list)
	for r in subset:
		by_scenario[r["scenario"]].append(r)

	for scenario, items in by_scenario.items():
		modes = sorted({r["mode"] for r in items})
		workloads = sorted({r["workload"] for r in items})
		fig, ax = plt.subplots(figsize=(10, 5))
		width = 0.8 / max(1, len(workloads))
		x = list(range(len(modes)))
		for i, wl in enumerate(workloads):
			vals = []
			for mode in modes:
				match = [r for r in items if r["workload"] == wl and r["mode"] == mode]
				vals.append(match[0]["repair_bytes_total"] if match else 0)
			ax.bar(
				[xi + i * width for xi in x],
				vals,
				width=width,
				label=wl,
			)
		ax.set_xticks([xi + (len(workloads) - 1) * width / 2 for xi in x])
		ax.set_xticklabels(modes, rotation=20, ha="right")
		ax.set_ylabel("Repair bytes (total)")
		ax.set_title(f"Repair overhead - {scenario}")
		ax.legend(fontsize=8)
		_save(fig, f"repair_overhead__{scenario}.png")


def plot_availability_timeline(
	avail: dict[tuple[str, str, str], list[tuple[float, float]]],
) -> None:
	by_scenario_wl: dict[tuple[str, str], dict[str, list[tuple[float, float]]]] = defaultdict(dict)
	for (mode, scenario, workload), pts in avail.items():
		by_scenario_wl[(scenario, workload)][mode] = pts

	for (scenario, workload), mode_pts in by_scenario_wl.items():
		if scenario not in ("node_crash_midrun", "background_repair_traffic"):
			continue
		fig, ax = plt.subplots(figsize=(10, 5))
		for mode, pts in sorted(mode_pts.items()):
			if not pts:
				continue
			xs = [t for t, _ in pts]
			ys = [a for _, a in pts]
			ax.plot(xs, ys, label=mode, marker="o", markersize=3, linewidth=1)
		ax.set_xlabel("Elapsed (s)")
		ax.set_ylabel("Window availability")
		ax.set_ylim(-0.05, 1.05)
		ax.set_title(f"Availability timeline - {scenario} / {workload}")
		ax.grid(True, alpha=0.3)
		ax.legend(fontsize=8)
		_save(fig, f"availability__{scenario}__{workload}.png")


def main() -> None:
	ap = argparse.ArgumentParser()
	ap.parse_args()
	rows = _read_aggregate()
	if not rows:
		print("no aggregate rows found; run scripts/run_experiments.py first")
		return
	plot_throughput_per_workload(rows)
	plot_latency_percentiles(rows)
	plot_repair_overhead(rows)
	plot_cdfs(_read_latencies())
	plot_availability_timeline(_read_availability())
	print("done")


if __name__ == "__main__":
	main()
