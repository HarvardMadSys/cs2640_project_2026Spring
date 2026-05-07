#!/usr/bin/env python3
"""Plot per-collection QPS curves from the run_sweep.py output.

For each collection in DEFAULT_COLLECTIONS, look in
`<HERE>/<collection>_bench_result/` for the three throughput CSVs:

  throughput_A_*.csv         (A solo, request-per-tenant 16 by default)
  throughput_B_*.csv         (B solo, request-per-tenant 16 by default)
  throughput_A_B_*.csv       (A+B concurrent, request-per-tenant 1)

and produce one PDF per collection at
`<HERE>/plots/qps_<collection>.pdf` with three time series:

  - A solo qps_A
  - B solo qps_B
  - A+B aggregate (qps_A + qps_B from the A+B run)

It also produces a single grouped bar chart at
`<HERE>/plots/qps_compact_vs_nocompact.pdf` comparing the steady-state
A+B aggregate QPS between the compacted and nocompact variants of the
three base collections, sourced from the run_A_B_*.csv files in
`<collection>_bench_result/` and `<collection>_nocompact_bench_result/`.

CSV columns (newer bench_tenants.py also writes sys_qps, but we compute
the sum here so older logs without sys_qps still plot correctly).

Usage:
    python plot_sweep.py
    python plot_sweep.py --collections wiki_QC02_SS02 wiki_QC08_SS08
    python plot_sweep.py --out-dir /tmp/sweep_plots
"""
from __future__ import annotations

import argparse
import csv
import sys
from glob import glob
from pathlib import Path

# Apply shared paper style; this is a global rule from CLAUDE.md.
sys.path.insert(0, "/home/yunjia/Desktop/scripts")
from plot_style import apply_style, PALETTE  # noqa: E402

import math  # noqa: E402

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import MultipleLocator  # noqa: E402


HERE = Path(__file__).resolve().parent
X_MAX = 120  # always plot 0..120s, even when CSVs go longer
Y_MIN_RANGE = 5  # y-axis spans at least 0..5 even when QPS is small

DEFAULT_COLLECTIONS = [
    "wiki_QC02_SS02",
    "wiki_QC02_SS02_nocompact",
    "wiki_QC02_SS08",
    "wiki_QC02_SS08_nocompact",
    "wiki_QC08_SS08",
    "wiki_QC08_SS08_nocompact",
]


def _find_csv(folder: Path, prefix: str) -> Path | None:
    """Return the most recent throughput CSV with the given prefix, or None."""
    matches = sorted(folder.glob(f"{prefix}_*.csv"), key=lambda p: p.stat().st_mtime)
    return matches[-1] if matches else None


def _read_csv(path: Path) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    with path.open() as f:
        rdr = csv.DictReader(f)
        for row in rdr:
            cleaned: dict[str, float] = {}
            for k, v in row.items():
                try:
                    cleaned[k] = float(v) if v not in ("", "nan") else float("nan")
                except ValueError:
                    pass
            out.append(cleaned)
    return out


def plot_collection(collection: str, bench_dir: Path, out_pdf: Path) -> bool:
    """Plot one collection. Returns True if any file was found."""
    a_csv  = _find_csv(bench_dir, "throughput_A")        # A solo (NOT A_B)
    b_csv  = _find_csv(bench_dir, "throughput_B")        # B solo
    ab_csv = _find_csv(bench_dir, "throughput_A_B")      # A+B concurrent

    # Glob would also match throughput_A_B_* under the throughput_A_ prefix,
    # so de-dup by file path:
    if a_csv and a_csv == ab_csv:
        a_csv = None
    if a_csv and "_A_B_" in a_csv.name:
        a_csv = None
    if b_csv and b_csv == ab_csv:
        b_csv = None
    if b_csv and "_A_B_" in b_csv.name:
        b_csv = None

    if not (a_csv or b_csv or ab_csv):
        return False

    fig, ax = plt.subplots(figsize=(8.0, 4.5))
    handles, labels = [], []
    y_seen: list[float] = []

    def _trim(rows: list[dict], y_key: str | None = None,
              y_extra: callable | None = None) -> tuple[list[float], list[float]]:
        ts: list[float] = []
        ys: list[float] = []
        for r in rows:
            t = r.get("t_rel_s", float("nan"))
            if t != t or t > X_MAX:
                continue
            if y_extra is not None:
                v = y_extra(r)
            else:
                v = r.get(y_key, float("nan"))
            ts.append(t); ys.append(v)
        return ts, ys

    if a_csv:
        ts, ys = _trim(_read_csv(a_csv), y_key="qps_A")
        line, = ax.plot(ts, ys, marker="o", color=PALETTE[0],
                        linewidth=2, markersize=4)
        handles.append(line); labels.append("A solo  (rpt=16)")
        y_seen.extend(v for v in ys if v == v)

    if b_csv:
        ts, ys = _trim(_read_csv(b_csv), y_key="qps_B")
        line, = ax.plot(ts, ys, marker="s", color=PALETTE[1],
                        linewidth=2, markersize=4)
        handles.append(line); labels.append("B solo  (rpt=16)")
        y_seen.extend(v for v in ys if v == v)

    if ab_csv:
        ts, ys = _trim(
            _read_csv(ab_csv),
            y_extra=lambda r: r.get("qps_A", 0.0) + r.get("qps_B", 0.0),
        )
        line, = ax.plot(ts, ys, marker="^", color=PALETTE[2],
                        linewidth=2, markersize=4)
        handles.append(line); labels.append("A+B concurrent total  (rpt=8+8)")
        y_seen.extend(v for v in ys if v == v)

    # Axes: always show 0..120s on x, and at least 0..5 on y with integer ticks.
    ax.set_xlim(0, X_MAX)
    y_max = max(y_seen) if y_seen else 0.0
    y_top = max(Y_MIN_RANGE, math.ceil(y_max * 1.1))
    ax.set_ylim(0, y_top)
    # Tick spacing: aim for ~6 to ~12 major ticks; step grows with y_top.
    if   y_top <= 10:  ystep = 1
    elif y_top <= 24:  ystep = 2
    elif y_top <= 60:  ystep = 5
    elif y_top <= 120: ystep = 10
    elif y_top <= 240: ystep = 20
    else:              ystep = 50
    ax.yaxis.set_major_locator(MultipleLocator(ystep))
    ax.xaxis.set_major_locator(MultipleLocator(20))

    ax.set_xlabel("time (s)")
    ax.set_ylabel("QPS (queries / sec)")
    ax.set_title(collection)
    ax.grid(True, linestyle="--", alpha=0.4)
    ax.legend(handles, labels, loc="best")
    fig.tight_layout()

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_pdf)
    plt.close(fig)
    return True


BASE_COLLECTIONS = [
    "wiki_QC02_SS02",
    "wiki_QC02_SS08",
    "wiki_QC08_SS08",
]


def _avg_ab_qps(rows: list[dict[str, float]], skip_s: float = 30.0) -> float:
    """Mean of (qps_A + qps_B) for samples after the warmup window.

    skip_s drops the first warmup rows, where the bench is still ramping
    up and recall/qps both swing.
    """
    vals: list[float] = []
    for r in rows:
        t = r.get("t_rel_s", float("nan"))
        if t != t or t < skip_s:
            continue
        a = r.get("qps_A", float("nan"))
        b = r.get("qps_B", float("nan"))
        if a != a or b != b:
            continue
        vals.append(a + b)
    if not vals:
        return float("nan")
    return sum(vals) / len(vals)


def _avg_qps_col(rows: list[dict[str, float]], col_key: str,
                 skip_s: float = 30.0) -> float:
    """Mean of one qps column (e.g. qps_A) past the warmup window."""
    vals: list[float] = []
    for r in rows:
        t = r.get("t_rel_s", float("nan"))
        if t != t or t < skip_s:
            continue
        v = r.get(col_key, float("nan"))
        if v != v:
            continue
        vals.append(v)
    if not vals:
        return float("nan")
    return sum(vals) / len(vals)


def plot_compact_vs_nocompact_bar(
    out_pdf: Path, base_collections: list[str], skip_s: float = 30.0,
) -> bool:
    """Grouped bar chart: compact vs nocompact A+B aggregate QPS.

    For each base collection (e.g. wiki_QC02_SS02), reads the A+B
    throughput CSV from both the bench_result and the nocompact
    bench_result directories, takes the mean of (qps_A + qps_B) over
    rows with t_rel_s >= skip_s, and plots the two values as a pair of
    bars at the same x position.
    """
    compact_qps: list[float] = []
    nocompact_qps: list[float] = []

    for col in base_collections:
        cdir = HERE / f"{col}_bench_result"
        ndir = HERE / f"{col}_nocompact_bench_result"
        c_csv = _find_csv(cdir, "throughput_A_B") if cdir.is_dir() else None
        n_csv = _find_csv(ndir, "throughput_A_B") if ndir.is_dir() else None
        compact_qps.append(
            _avg_ab_qps(_read_csv(c_csv), skip_s) if c_csv else float("nan"))
        nocompact_qps.append(
            _avg_ab_qps(_read_csv(n_csv), skip_s) if n_csv else float("nan"))

    if all(v != v for v in compact_qps + nocompact_qps):
        return False

    fig, ax = plt.subplots(figsize=(7.0, 4.5))
    width = 0.36
    positions = list(range(len(base_collections)))
    bars_c = ax.bar(
        [p - width / 2 for p in positions],
        [0.0 if v != v else v for v in compact_qps],
        width=width, color=PALETTE[0], label="compact",
    )
    bars_n = ax.bar(
        [p + width / 2 for p in positions],
        [0.0 if v != v else v for v in nocompact_qps],
        width=width, color=PALETTE[1], label="nocompact",
    )

    for bars, vals in ((bars_c, compact_qps), (bars_n, nocompact_qps)):
        for b, v in zip(bars, vals):
            if v != v:
                continue
            ax.text(b.get_x() + b.get_width() / 2, b.get_height(),
                    f"{v:.2f}", ha="center", va="bottom", fontsize=9)

    ax.set_xticks(positions)
    ax.set_xticklabels(base_collections)
    ax.set_ylabel(f"avg A+B QPS")
    ax.set_title("A+B aggregate QPS, compact vs nocompact")
    ax.legend(loc="best")
    ax.grid(True, linestyle="--", alpha=0.4, axis="y")

    y_max_seen = max(
        (v for v in compact_qps + nocompact_qps if v == v),
        default=0.0,
    )
    ax.set_ylim(0, max(Y_MIN_RANGE, math.ceil(y_max_seen * 1.18)))
    fig.tight_layout()

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_pdf)
    plt.close(fig)
    return True


def plot_compact_solo_vs_concurrent_bar(
    out_pdf: Path, base_collections: list[str], skip_s: float = 30.0,
) -> bool:
    """Grouped bar chart of three values per collection (compact only):

      A solo qps     mean of qps_A from throughput_A_*.csv
      B solo qps     mean of qps_B from throughput_B_*.csv
      A+B aggregate  mean of (qps_A + qps_B) from throughput_A_B_*.csv

    All three averages are taken over rows with t_rel_s >= skip_s so the
    warmup ramp is excluded.
    """
    a_qps: list[float] = []
    b_qps: list[float] = []
    ab_qps: list[float] = []

    for col in base_collections:
        bdir = HERE / f"{col}_bench_result"
        if not bdir.is_dir():
            a_qps.append(float("nan"))
            b_qps.append(float("nan"))
            ab_qps.append(float("nan"))
            continue
        a_csv = _find_csv(bdir, "throughput_A")
        b_csv = _find_csv(bdir, "throughput_B")
        ab_csv = _find_csv(bdir, "throughput_A_B")
        # _find_csv with prefix "throughput_A" also matches throughput_A_B_*,
        # so de-dup the same way plot_collection does.
        if a_csv and (a_csv == ab_csv or "_A_B_" in a_csv.name):
            a_csv = None
        if b_csv and (b_csv == ab_csv or "_A_B_" in b_csv.name):
            b_csv = None
        a_qps.append(_avg_qps_col(_read_csv(a_csv), "qps_A", skip_s)
                     if a_csv else float("nan"))
        b_qps.append(_avg_qps_col(_read_csv(b_csv), "qps_B", skip_s)
                     if b_csv else float("nan"))
        ab_qps.append(_avg_ab_qps(_read_csv(ab_csv), skip_s)
                      if ab_csv else float("nan"))

    if all(v != v for v in a_qps + b_qps + ab_qps):
        return False

    fig, ax = plt.subplots(figsize=(8.0, 4.5))
    width = 0.27
    positions = list(range(len(base_collections)))
    bars_a = ax.bar(
        [p - width for p in positions],
        [0.0 if v != v else v for v in a_qps],
        width=width, color=PALETTE[0], label="A solo  (rpt=16)",
    )
    bars_b = ax.bar(
        positions,
        [0.0 if v != v else v for v in b_qps],
        width=width, color=PALETTE[1], label="B solo  (rpt=16)",
    )
    bars_ab = ax.bar(
        [p + width for p in positions],
        [0.0 if v != v else v for v in ab_qps],
        width=width, color=PALETTE[2], label="A+B aggregate  (rpt=8+8)",
    )

    for bars, vals in ((bars_a, a_qps), (bars_b, b_qps), (bars_ab, ab_qps)):
        for b, v in zip(bars, vals):
            if v != v:
                continue
            ax.text(b.get_x() + b.get_width() / 2, b.get_height(),
                    f"{v:.2f}", ha="center", va="bottom", fontsize=9)

    ax.set_xticks(positions)
    ax.set_xticklabels(base_collections)
    ax.set_ylabel(f"avg QPS")
    ax.set_title("Compact: A solo, B solo, A+B aggregate")
    ax.legend(loc="best")
    ax.grid(True, linestyle="--", alpha=0.4, axis="y")

    y_max_seen = max(
        (v for v in a_qps + b_qps + ab_qps if v == v),
        default=0.0,
    )
    ax.set_ylim(0, max(Y_MIN_RANGE, math.ceil(y_max_seen * 1.18)))
    fig.tight_layout()

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_pdf)
    plt.close(fig)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--collections", nargs="+", default=DEFAULT_COLLECTIONS,
                    help="Collections to plot (default: all six wiki variants)")
    ap.add_argument("--out-dir", default=str(HERE / "plots"),
                    help="Output directory for the PDFs")
    args = ap.parse_args()

    apply_style()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Writing PDFs to {out_dir}")
    n_ok = 0
    for col in args.collections:
        bench_dir = HERE / f"{col}_bench_result"
        if not bench_dir.is_dir():
            print(f"  [skip] {col}: missing {bench_dir.name}/")
            continue
        out_pdf = out_dir / f"qps_{col}.pdf"
        if plot_collection(col, bench_dir, out_pdf):
            print(f"  [ok ] {col} -> {out_pdf.name}")
            n_ok += 1
        else:
            print(f"  [skip] {col}: no throughput_*.csv files in {bench_dir.name}/")
    print(f"Wrote {n_ok}/{len(args.collections)} PDFs.")

    bar_pdf = out_dir / "qps_compact_vs_nocompact.pdf"
    if plot_compact_vs_nocompact_bar(bar_pdf, BASE_COLLECTIONS):
        print(f"  [ok ] compact-vs-nocompact bar -> {bar_pdf.name}")
    else:
        print(f"  [skip] compact-vs-nocompact bar: no A+B throughput "
              f"CSVs found for any base collection")

    solo_pdf = out_dir / "qps_compact_solo_vs_concurrent.pdf"
    if plot_compact_solo_vs_concurrent_bar(solo_pdf, BASE_COLLECTIONS):
        print(f"  [ok ] compact solo-vs-concurrent bar -> {solo_pdf.name}")
    else:
        print(f"  [skip] compact solo-vs-concurrent bar: no throughput "
              f"CSVs found for any base collection")

    return 0 if n_ok else 1


if __name__ == "__main__":
    sys.exit(main())
