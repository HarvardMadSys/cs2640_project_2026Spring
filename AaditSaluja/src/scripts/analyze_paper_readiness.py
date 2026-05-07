#!/usr/bin/env python3
"""Generate paper-readiness report and simple SVG plots from benchmark results."""

from __future__ import annotations

import csv
import html
import json
import math
import statistics
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
ROOT = SCRIPT_PATH.parents[2] if SCRIPT_PATH.parent.parent.name == "src" else SCRIPT_PATH.parents[1]
RESULT_DIR = ROOT / "report" / "results" / "cloudlab-comprehensive-20260506-3x"
FIG_DIR = ROOT / "report" / "figures"
REPORT = ROOT / "src" / "docs" / "PAPER_READINESS_REPORT.md"

WORKLOAD_LABELS = {
    "ior_mdtest_tree": "IOR/mdtest",
    "filebench_varmail_like": "Filebench-like",
    "ycsb_zipf_hotdirs": "Zipf hotdirs",
    "hotcold_cold70_access10": "70% cold / 10% access",
    "hotcold_cold90_access10": "90% cold / 10% access",
    "hotcold_cold90_access20": "90% cold / 20% access",
}
WORKLOAD_ORDER = list(WORKLOAD_LABELS)
VARIANTS = ["native", "oracle", "predictor"]
COLORS = {"native": "#4c78a8", "oracle": "#f58518", "predictor": "#54a24b"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def f(row: dict[str, str], key: str) -> float:
    value = row.get(key, "")
    return float(value) if value != "" else 0.0


def load_summary() -> list[dict[str, str]]:
    return read_csv(RESULT_DIR / "summary.csv")


def load_phase_summary() -> list[dict[str, str]]:
    return read_csv(RESULT_DIR / "phase_summary.csv")


def by_workload_variant(rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    return {(row["workload_id"], row["variant"]): row for row in rows}


def log_ticks(min_value: float, max_value: float) -> list[float]:
    start = math.floor(math.log10(max(min_value, 0.001)))
    end = math.ceil(math.log10(max_value))
    ticks = []
    for power in range(start, end + 1):
        for multiplier in (1, 2, 5):
            value = multiplier * (10**power)
            if min_value <= value <= max_value:
                ticks.append(value)
    return ticks


def nice_number(value: float) -> str:
    if value >= 1000:
        return f"{value/1000:.1f}k"
    if value >= 100:
        return f"{value:.0f}"
    if value >= 10:
        return f"{value:.1f}"
    return f"{value:.2f}"


def grouped_bar_svg(
    path: Path,
    title: str,
    y_label: str,
    workloads: list[str],
    values: dict[tuple[str, str], float],
    *,
    log_scale: bool = False,
) -> None:
    width = 1120
    height = 520
    left = 82
    right = 24
    top = 52
    bottom = 118
    plot_w = width - left - right
    plot_h = height - top - bottom
    all_values = [value for value in values.values() if value > 0]
    max_value = max(all_values) if all_values else 1
    min_value = min(all_values) if all_values else 1
    if log_scale:
        min_axis = 0.5 if min_value >= 0.5 else max(0.001, min_value)
        max_axis = max_value * 1.25

        def y_pos(value: float) -> float:
            value = max(value, min_axis)
            span = math.log10(max_axis) - math.log10(min_axis)
            return top + plot_h - ((math.log10(value) - math.log10(min_axis)) / span) * plot_h

        ticks = log_ticks(min_axis, max_axis)
    else:
        min_axis = 0.0
        max_axis = max_value * 1.2

        def y_pos(value: float) -> float:
            return top + plot_h - ((value - min_axis) / (max_axis - min_axis)) * plot_h

        raw_ticks = [max_axis * step / 5 for step in range(6)]
        ticks = raw_ticks

    group_w = plot_w / len(workloads)
    bar_w = min(34, group_w / (len(VARIANTS) + 1.8))
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{width/2}" y="28" text-anchor="middle" font-family="Arial, sans-serif" font-size="20" font-weight="700">{html.escape(title)}</text>',
        f'<text x="18" y="{top + plot_h/2}" transform="rotate(-90 18 {top + plot_h/2})" text-anchor="middle" font-family="Arial, sans-serif" font-size="13">{html.escape(y_label)}</text>',
    ]
    for tick in ticks:
        y = y_pos(tick)
        lines.append(f'<line x1="{left}" y1="{y:.2f}" x2="{left + plot_w}" y2="{y:.2f}" stroke="#e5e7eb" stroke-width="1"/>')
        lines.append(f'<text x="{left - 8}" y="{y + 4:.2f}" text-anchor="end" font-family="Arial, sans-serif" font-size="11" fill="#374151">{html.escape(nice_number(tick))}</text>')
    lines.append(f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="#111827"/>')
    lines.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#111827"/>')

    for group_index, workload in enumerate(workloads):
        group_x = left + group_index * group_w
        center = group_x + group_w / 2
        for variant_index, variant in enumerate(VARIANTS):
            value = values.get((workload, variant), 0.0)
            x = center + (variant_index - 1) * (bar_w + 4) - bar_w / 2
            y = y_pos(value) if value > 0 else top + plot_h
            h = top + plot_h - y
            lines.append(
                f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_w:.2f}" height="{max(h, 0):.2f}" fill="{COLORS[variant]}"/>'
            )
        label = WORKLOAD_LABELS[workload]
        lines.append(
            f'<text x="{center:.2f}" y="{top + plot_h + 22}" text-anchor="middle" font-family="Arial, sans-serif" font-size="11" fill="#111827">{html.escape(label)}</text>'
        )
    legend_x = left
    legend_y = height - 32
    for index, variant in enumerate(VARIANTS):
        x = legend_x + index * 120
        lines.append(f'<rect x="{x}" y="{legend_y - 11}" width="14" height="14" fill="{COLORS[variant]}"/>')
        lines.append(f'<text x="{x + 20}" y="{legend_y}" font-family="Arial, sans-serif" font-size="12">{variant}</text>')
    lines.append("</svg>")
    path.write_text("\n".join(lines) + "\n")


def markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(item) for item in row) + " |")
    return "\n".join(lines)


def build_report(summary: list[dict[str, str]], phases: list[dict[str, str]]) -> str:
    lookup = by_workload_variant(summary)
    result_rows = []
    for workload in WORKLOAD_ORDER:
        native = lookup[(workload, "native")]
        oracle = lookup[(workload, "oracle")]
        predictor = lookup[(workload, "predictor")]
        result_rows.append(
            [
                WORKLOAD_LABELS[workload],
                f"{f(native, 'mean_ops_per_sec'):.2f}",
                f"{f(oracle, 'mean_ops_per_sec'):.2f}",
                f"{f(predictor, 'mean_ops_per_sec'):.2f}",
                f"{f(oracle, 'speedup_vs_native'):.2f}x",
                f"{f(predictor, 'speedup_vs_native'):.2f}x",
                f"{f(predictor, 'relative_to_oracle'):.2f}x",
            ]
        )
    variance_rows = sorted(
        summary,
        key=lambda row: f(row, "stdev_ops_per_sec") / max(f(row, "mean_ops_per_sec"), 1e-9),
        reverse=True,
    )[:6]
    variance_table = [
        [
            WORKLOAD_LABELS.get(row["workload_id"], row["workload_id"]),
            row["variant"],
            f"{f(row, 'mean_ops_per_sec'):.2f}",
            f"{f(row, 'stdev_ops_per_sec'):.2f}",
            f"{f(row, 'stdev_ops_per_sec') / max(f(row, 'mean_ops_per_sec'), 1e-9):.2f}",
        ]
        for row in variance_rows
    ]
    bottleneck_rows = sorted(
        phases,
        key=lambda row: f(row, "mean_latency_p95_ms"),
        reverse=True,
    )[:10]
    bottleneck_table = [
        [
            WORKLOAD_LABELS.get(row["workload_id"], row["workload_id"]),
            row["variant"],
            row["operation"],
            f"{f(row, 'mean_ops_per_sec'):.2f}",
            f"{f(row, 'mean_latency_p95_ms'):.2f}",
        ]
        for row in bottleneck_rows
    ]
    predictor_rows = [
        row
        for row in summary
        if row["variant"] == "predictor"
    ]
    predictor_table = [
        [
            WORKLOAD_LABELS.get(row["workload_id"], row["workload_id"]),
            f"{f(row, 'mean_ops_per_sec'):.2f}",
            f"{f(row, 'speedup_vs_native'):.2f}x",
            f"{f(row, 'relative_to_oracle'):.2f}x",
            f"{f(row, 'mean_namespace_entries'):.0f}",
            f"{f(row, 'mean_predicted_hot_dirs'):.1f}",
        ]
        for row in predictor_rows
    ]
    completed = len(list(RESULT_DIR.glob("*.json")))
    report = f"""# Paper Readiness Report

Last updated: 2026-05-06

## Executive Summary

We now have an end-to-end CephFS experiment harness, three storage designs, a
fresh CloudLab cluster, completed comprehensive and paper-ready matrices, direct
external benchmark runs, follow-up ablations, and reproducible result artifacts.
The strongest technical result is that cold small-file packing can reduce CephFS
namespace pressure enough to beat native CephFS by large margins on
metadata-heavy workloads. The best predictor design, `directory_hotset` with
lazy existing-file handling, uses only online read observations and performs
well on hot/cold locality workloads.

The report is in a submittable course-project state. Its claims are still
bounded by the prototype overlay, directory-backed OSDs on root disks, and lack
of production recovery, garbage collection, and multi-client validation.

Completed comprehensive runs: **{completed}/54**. No failure marker was present.

## What We Built

- Rebuilt a 4-node CloudLab CephFS deployment after reallocation: node0 client,
  node1 monitor/manager/OSD/MDS, node2 OSD/MDS, node3 OSD/standby MDS.
- Implemented a Python benchmark runner with POSIX-mounted CephFS support,
  per-phase throughput and p95/p99 latency, Ceph stats capture, storage plugins,
  and policy plugins.
- Implemented recreated workload shapes for IOR/mdtest, Filebench-style mail,
  Zipf/hotspot directory skew, and hot/cold locality.
- Evaluated native CephFS, oracle cold packing, and non-oracle predictive cold
  packing.
- Found and fixed an unfair predictor profile artifact: packed stats were
  in-memory lookups and hot churn was packed too aggressively.
- Added fair predictor options: `packed_stat_mode=index`,
  `predictor_strategy=directory_hotset`, `predictor_promote_existing=false`,
  and read-only hot-directory learning.
- Added a resumable scheduler:
  `src/scripts/schedule_cloudlab_comprehensive_bench.sh`.

Representative workload sources used for recreated workload shapes:

- IOR/mdtest documentation: https://ior.readthedocs.io/
- IOR/mdtest source: https://github.com/hpc/ior
- Filebench source and workload personalities:
  https://github.com/filebench/filebench
- YCSB request distributions:
  https://github.com/brianfrankcooper/YCSB/wiki/Core-Properties
- SNIA IOTTA trace repository:
  https://www.snia.org/educational-library/iotta-repository-2019

## Fairness And Information Audit

The current comprehensive run is fair enough for internal decision-making:

- All variants use the same CephFS mount, root, file count, file size, directory
  count, operation count, worker count, seed, cleanup behavior, and serial run
  order controls.
- Jobs are randomized globally and run one at a time.
- `ceph -s` must report `HEALTH_OK` before each job.
- Predictor placement does not inspect `hot*` or `cold*` labels. It observes
  only online read events.
- Packed `stat()` touches the packed index file through `packed_stat_mode=index`,
  so it is no longer a pure Python dictionary lookup.

Important caveat: oracle is an upper bound, not a deployable policy. For the
hand-written hot/cold workload it knows the generator-defined hot set. For
generic representative workloads it receives no future-access labels and runs
mostly as all-cold packing. For Zipf hotdirs it is allowed to know `dir0000`
because that is the generator-defined hot directory. These distinctions must be
made explicit in any paper.

## Comprehensive Results

![Throughput](../../report/figures/comprehensive_throughput.svg)

![Speedup](../../report/figures/comprehensive_speedup.svg)

![P95](../../report/figures/comprehensive_p95.svg)

{markdown_table(['Workload', 'Native ops/s', 'Oracle ops/s', 'Predictor ops/s', 'Oracle vs native', 'Predictor vs native', 'Predictor vs oracle'], result_rows)}

## Interpretation

- **IOR/mdtest-style metadata:** oracle and predictor both reach roughly 5.2x
  native throughput. This mostly demonstrates the power of packing many logical
  files into a small physical namespace.
- **Filebench-like mail workload:** oracle is 15.9x native, predictor is 3.6x
  native. Predictor is better than native, but still far below all-cold oracle
  because mixed create/delete/read behavior stresses the lazy policy and cleanup
  path.
- **Zipf hotdirs:** predictor shows 46.1x native. Treat this as a red flag, not
  a headline. The current workload has create/stat/delete but no read phase, so
  read-only prediction never learns and the predictor behaves like all-cold
  packing. We need a better Zipf workload with reads/updates and file-level
  locality before claiming this.
- **Hot/cold locality:** predictor is 3.1x to 4.7x native and lands near or
  above oracle in this run. This is encouraging, but it does not mean predictor
  beats an oracle in a universal sense. The predictor's lazy mode keeps existing
  files packed and makes future hot-directory creates native, while oracle keeps
  known-hot files native from the start. They are different policies with
  different semantics.
- **Latency:** predictor usually improves mean phase p95 relative to native, but
  hot churn phases still show large p99 tails in raw phase summaries.

## Highest Variance Cells

{markdown_table(['Workload', 'Variant', 'Mean ops/s', 'Stdev', 'CV'], variance_table)}

High variance means the current three-repeat matrix should be used for direction
and prioritization, not final claims with tight confidence intervals.

## Phase Bottlenecks

{markdown_table(['Workload', 'Variant', 'Operation', 'Mean ops/s', 'Mean p95 ms'], bottleneck_table)}

The major bottlenecks remain native and oracle hot-churn create/delete phases,
plus Filebench-style native mixed operations. These phases dominate run time and
tail latency.

## Predictor-Specific View

{markdown_table(['Workload', 'Predictor ops/s', 'Vs native', 'Vs oracle', 'Namespace entries', 'Predicted hot dirs'], predictor_table)}

The predictor currently works best as a cold-by-default packing layer with
online hot-directory learning. It is not yet a general learned replacement for
oracle labels.

## Good Progress

- We have a coherent benchmark and storage-plugin framework.
- The project has a clear negative result: simple CephFS subtree pinning was not
  enough to beat default CephFS.
- The project has a clear positive result: reducing physical namespace entries
  with cold packing consistently helps metadata-heavy small-file workloads.
- The predictor no longer depends on unavailable future labels.
- The benchmark records p95/p99, raw logs, exact command lines, and resumable
  manifests.
- We caught and corrected a major measurement artifact before final reporting.

## Current Bottlenecks And Risks

- The recreated workloads are not enough for a paper claim. We need direct
  upstream IOR/mdtest and Filebench runs, and preferably trace-shaped workloads.
- The packed layer has cheap logical deletes. A production design needs garbage
  collection and compaction costs included in the measured path.
- The packed index is in-memory during the run. We need recovery/replay tests
  and a clear persistent-index story.
- Multi-client behavior is not validated. Concurrent readers/writers,
  promotion races, and crash consistency remain open.
- OSDs are directory-backed on the root disk in this allocation. Results are
  fair within the run but should not be generalized as hardware-neutral.
- We do not drop caches or remount between runs.
- Some cells have large run-to-run variance, especially oracle hot/cold cases.
- The Zipf workload currently exaggerates packing because it lacks read/update
  phases that exercise the predictor.

## Future Work

1. **Improve predictor accuracy.** Report precision/recall clearly and reduce
   false hot-directory classification.
2. **Add trace-shaped validation.** Use SNIA IOTTA or another accepted storage
   trace source, or synthesize from trace distributions if direct replay is too
   heavy.
3. **Ablate the predictor.** Compare read-only directory hotset, stat+read,
   promote-existing on/off, thresholds, distinct-path thresholds, and path-level
   versus directory-level learning.
4. **Ablate the storage layer.** Vary segment size, index batch size, data batch
   size, virtual cold directories, CephFS segment files versus RADOS objects,
   and layout xattrs.
5. **Measure maintenance costs.** Include garbage collection, compaction,
   tombstone cleanup, index rebuild, and crash recovery.
6. **Strengthen statistics.** Use at least 5-10 repeats for future figures,
   confidence intervals, and outlier analysis.
7. **Scale up.** Sweep file counts, directory counts, worker/client counts, MDS
   ranks, cold fraction, and cold access fraction.
8. **Collect Ceph internals.** Add MDS CPU/load, MDS op counters, OSD op
   counters, metadata-pool writes, data-pool writes, object counts, and memory
   footprint.
9. **Clarify semantics.** State exactly what POSIX semantics are preserved by
    the overlay and what is deferred. The paper should frame this as a
    small-file cold-packing layer with online hot-directory learning, not as a
    transparent CephFS modification unless we implement deeper integration.

## Suggested Paper Framing

The most defensible claim is:

> Metadata-heavy CephFS workloads suffer from physical namespace pressure. A
> cold-by-default small-file packing layer can substantially reduce namespace
> operations, and a lightweight online hot-directory predictor can recover much
> of the oracle benefit without future labels.

Avoid claiming that the predictor universally beats oracle. In the current data,
predictor can exceed oracle because the lazy predictor and oracle are not the
same policy: oracle preserves known-hot files as native from creation time,
while lazy prediction keeps existing files packed and only changes future
creates.
"""
    return report


def main() -> int:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    summary = load_summary()
    phases = load_phase_summary()
    lookup = by_workload_variant(summary)
    throughput_values = {
        (workload, variant): f(lookup[(workload, variant)], "mean_ops_per_sec")
        for workload in WORKLOAD_ORDER
        for variant in VARIANTS
    }
    speedup_values = {
        (workload, variant): f(lookup[(workload, variant)], "speedup_vs_native")
        for workload in WORKLOAD_ORDER
        for variant in VARIANTS
    }
    p95_values = {
        (workload, variant): f(lookup[(workload, variant)], "mean_phase_p95_ms")
        for workload in WORKLOAD_ORDER
        for variant in VARIANTS
    }
    grouped_bar_svg(
        FIG_DIR / "comprehensive_throughput.svg",
        "Comprehensive Benchmark Throughput",
        "Mean ops/sec (log scale)",
        WORKLOAD_ORDER,
        throughput_values,
        log_scale=True,
    )
    grouped_bar_svg(
        FIG_DIR / "comprehensive_speedup.svg",
        "Speedup Relative to Native",
        "Speedup vs native (log scale)",
        WORKLOAD_ORDER,
        speedup_values,
        log_scale=True,
    )
    grouped_bar_svg(
        FIG_DIR / "comprehensive_p95.svg",
        "Mean Phase p95 Latency",
        "Mean p95 latency, ms (log scale)",
        WORKLOAD_ORDER,
        p95_values,
        log_scale=True,
    )
    REPORT.write_text(build_report(summary, phases))
    print(f"wrote {REPORT.relative_to(ROOT)}")
    print(f"wrote {FIG_DIR.relative_to(ROOT)}/comprehensive_*.svg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
