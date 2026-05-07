#!/usr/bin/env python3
"""Generate paper-ready PDF figures with error bars from benchmark summaries."""

from __future__ import annotations

import argparse
import csv
import math
import re
import shutil
import subprocess
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
ROOT = SCRIPT_PATH.parents[2] if SCRIPT_PATH.parent.parent.name == "src" else SCRIPT_PATH.parents[1]
DEFAULT_RESULT_DIR = ROOT / "report" / "results" / "cloudlab-paperready-20260506-3x"
DEFAULT_FIG_DIR = ROOT / "report" / "figures"

CORE_WORKLOAD_ORDER = [
    "recreated_mdtest_tree",
    "recreated_filebench_varmail_like",
    "ycsb_zipfian_file_skew",
    "ycsb_hotspot_file_skew",
    "hotcold_cold90_access10",
]
WORKLOAD_LABELS = {
    "recreated_mdtest_tree": "Recreated mdtest",
    "recreated_filebench_varmail_like": "Recreated varmail",
    "ycsb_zipfian_file_skew": "YCSB Zipfian",
    "ycsb_hotspot_file_skew": "YCSB hotspot",
    "hotcold_cold90_access10": "Hot/cold 90/10",
    "predictor_false_hot_churn": "False-hot churn",
    "scaled_hotcold_cold90_access10_20k": "Scaled hot/cold 20k",
}
EXTERNAL_ORDER = [
    "direct_mdtest",
    "direct_mdtest_10k",
    "direct_filebench_fileserver",
    "direct_filebench_varmail",
    "direct_ior",
    "direct_ior_512m",
]
EXTERNAL_LABELS = {
    "direct_mdtest": "mdtest 3k",
    "direct_mdtest_10k": "mdtest 10k",
    "direct_filebench_fileserver": "Filebench fileserver",
    "direct_filebench_varmail": "Filebench varmail",
    "direct_ior": "IOR 16 MiB",
    "direct_ior_512m": "IOR 512 MiB",
}
VARIANT_LABELS = {
    "native": "Native",
    "oracle": "Oracle packing",
    "predictor": "Predictor",
    "predictor_nolearn": "No-learning packing",
    "external": "Upstream benchmark",
}
VARIANT_COLORS = {
    "native": "blue!65!black",
    "oracle": "orange!85!black",
    "predictor": "green!55!black",
    "predictor_nolearn": "gray!65!black",
    "external": "blue!65!black",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def number(row: dict[str, str], key: str) -> float:
    value = row.get(key, "")
    return float(value) if value not in {"", None} else 0.0


def tex_escape(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in value)


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")


def axis_bounds(values: list[tuple[float, float]], log_scale: bool) -> tuple[float, float]:
    positive = [(mean, stdev) for mean, stdev in values if mean > 0]
    if not positive:
        return 0.0, 1.0
    if log_scale:
        lower = min(max(0.1, mean - stdev) for mean, stdev in positive)
        upper = max(mean + stdev for mean, stdev in positive)
        return max(0.1, lower * 0.65), upper * 1.8
    upper = max(mean + stdev for mean, stdev in positive)
    return 0.0, upper * 1.25


def write_grouped_bar_tex(
    tex_path: Path,
    *,
    title: str,
    ylabel: str,
    workloads: list[str],
    labels: dict[str, str],
    variants: list[str],
    values: dict[tuple[str, str], tuple[float, float]],
    log_scale: bool = True,
) -> None:
    all_values = [values[key] for key in values if key[0] in workloads and key[1] in variants]
    ymin, ymax = axis_bounds(all_values, log_scale)
    xticklabels = ",".join(tex_escape(labels.get(workload, workload)) for workload in workloads)
    xticks = ",".join(str(index) for index in range(len(workloads)))
    width = "7.2in" if len(workloads) <= 6 else "8.2in"
    bar_width = 7
    spacing = 9
    midpoint = (len(variants) - 1) / 2

    lines = [
        r"\documentclass[tikz,border=4pt]{standalone}",
        r"\usepackage{pgfplots}",
        r"\pgfplotsset{compat=1.18}",
        r"\begin{document}",
        r"\begin{tikzpicture}",
        r"\begin{axis}[",
        f"  title={{{tex_escape(title)}}},",
        f"  width={width},",
        r"  height=3.7in,",
        r"  ybar,",
        f"  bar width={bar_width}pt,",
        r"  enlarge x limits=0.10,",
        f"  ylabel={{{tex_escape(ylabel)}}},",
        f"  ymin={ymin:.6g},",
        f"  ymax={ymax:.6g},",
        "  ymode=log," if log_scale else r"  yticklabel style={/pgf/number format/fixed,/pgf/number format/precision=2},",
        f"  xtick={{{xticks}}},",
        f"  xticklabels={{{xticklabels}}},",
        r"  x tick label style={rotate=30,anchor=east,font=\scriptsize},",
        r"  ymajorgrids=true,",
        r"  grid style={draw=gray!20},",
        r"  legend style={at={(0.5,-0.30)},anchor=north,legend columns=2,font=\scriptsize,draw=none},",
        r"  error bars/y dir=both,",
        r"  error bars/y explicit,",
        r"]",
    ]
    for variant_index, variant in enumerate(variants):
        shift = (variant_index - midpoint) * spacing
        coordinates = []
        for workload_index, workload in enumerate(workloads):
            mean, stdev = values.get((workload, variant), (0.0, 0.0))
            if mean <= 0:
                continue
            error = min(stdev, max(0.0, mean - max(ymin * 1.02, 1e-9)))
            coordinates.append(
                f"({workload_index},{mean:.6g}) +- (0,{error:.6g})"
            )
        if not coordinates:
            continue
        lines.extend(
            [
                rf"\addplot+[fill={VARIANT_COLORS.get(variant, 'gray')},draw=black!55,bar shift={shift:.1f}pt]",
                "coordinates {",
                "  " + "\n  ".join(coordinates),
                "};",
                rf"\addlegendentry{{{tex_escape(VARIANT_LABELS.get(variant, variant))}}}",
            ]
        )
    lines.extend([r"\end{axis}", r"\end{tikzpicture}", r"\end{document}", ""])
    tex_path.write_text("\n".join(line for line in lines if line != ""))


def compile_tex(tex_path: Path) -> None:
    if shutil.which("latexmk"):
        command = ["latexmk", "-pdf", "-interaction=nonstopmode", tex_path.name]
    else:
        command = ["pdflatex", "-interaction=nonstopmode", tex_path.name]
    subprocess.run(command, cwd=tex_path.parent, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def build_figures(result_dir: Path, fig_dir: Path) -> list[Path]:
    summary = read_csv(result_dir / "summary.csv")
    fig_dir.mkdir(parents=True, exist_ok=True)

    rows_by_key = {
        (row["result_kind"], row["workload_id"], row["variant"]): row
        for row in summary
    }
    inrepo_workloads = [
        workload
        for workload in CORE_WORKLOAD_ORDER
        if any(("inrepo", workload, variant) in rows_by_key for variant in VARIANT_LABELS)
    ]
    followup_workloads = [
        workload
        for workload in ("predictor_false_hot_churn", "scaled_hotcold_cold90_access10_20k")
        if any(("inrepo", workload, variant) in rows_by_key for variant in VARIANT_LABELS)
    ]
    variants = [
        variant
        for variant in ("native", "oracle", "predictor", "predictor_nolearn")
        if any(("inrepo", workload, variant) in rows_by_key for workload in inrepo_workloads + followup_workloads)
    ]
    inrepo_values = {
        (workload, variant): (
            number(rows_by_key[("inrepo", workload, variant)], "mean_ops_per_sec"),
            number(rows_by_key[("inrepo", workload, variant)], "stdev_ops_per_sec"),
        )
        for workload in inrepo_workloads + followup_workloads
        for variant in variants
        if ("inrepo", workload, variant) in rows_by_key
    }

    generated: list[Path] = []
    tex_path = fig_dir / "paperready_inrepo_throughput_errorbars.tex"
    write_grouped_bar_tex(
        tex_path,
        title="In-repo policy comparison throughput",
        ylabel="Mean measured ops/s (log scale)",
        workloads=inrepo_workloads,
        labels=WORKLOAD_LABELS,
        variants=[variant for variant in variants if variant != "predictor_nolearn"],
        values=inrepo_values,
        log_scale=True,
    )
    compile_tex(tex_path)
    generated.append(tex_path.with_suffix(".pdf"))

    if followup_workloads:
        tex_path = fig_dir / "paperready_followup_throughput_errorbars.tex"
        write_grouped_bar_tex(
            tex_path,
            title="Follow-up downside and scale tests",
            ylabel="Mean measured ops/s (log scale)",
            workloads=followup_workloads,
            labels=WORKLOAD_LABELS,
            variants=variants,
            values=inrepo_values,
            log_scale=True,
        )
        compile_tex(tex_path)
        generated.append(tex_path.with_suffix(".pdf"))

    external_workloads = [
        workload
        for workload in EXTERNAL_ORDER
        if ("external", workload, "external") in rows_by_key
    ]
    external_values = {
        (workload, "external"): (
            number(rows_by_key[("external", workload, "external")], "mean_ops_per_sec"),
            number(rows_by_key[("external", workload, "external")], "stdev_ops_per_sec"),
        )
        for workload in external_workloads
    }
    tex_path = fig_dir / "paperready_external_throughput_errorbars.tex"
    write_grouped_bar_tex(
        tex_path,
        title="Direct upstream benchmark throughput",
        ylabel="Mean ops/s (log scale)",
        workloads=external_workloads,
        labels=EXTERNAL_LABELS,
        variants=["external"],
        values=external_values,
        log_scale=True,
    )
    compile_tex(tex_path)
    generated.append(tex_path.with_suffix(".pdf"))

    precision_values = {
        (row["workload_id"], "predictor"): (
            number(row, "mean_predicted_hot_dir_precision"),
            0.0,
        )
        for row in summary
        if row["result_kind"] == "inrepo"
        and row["variant"] == "predictor"
        and row.get("mean_predicted_hot_dir_precision", "") != ""
        and row["workload_id"] in inrepo_workloads + followup_workloads
    }
    if precision_values:
        precision_workloads = [
            workload
            for workload in inrepo_workloads + followup_workloads
            if (workload, "predictor") in precision_values
        ]
        tex_path = fig_dir / "paperready_predictor_precision_errorbars.tex"
        write_grouped_bar_tex(
            tex_path,
            title="Predictor hot-directory precision",
            ylabel="Mean precision",
            workloads=precision_workloads,
            labels=WORKLOAD_LABELS,
            variants=["predictor"],
            values=precision_values,
            log_scale=False,
        )
        compile_tex(tex_path)
        generated.append(tex_path.with_suffix(".pdf"))

    return generated


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-dir", type=Path, default=DEFAULT_RESULT_DIR)
    parser.add_argument("--fig-dir", type=Path, default=DEFAULT_FIG_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    generated = build_figures(args.result_dir, args.fig_dir)
    for path in generated:
        print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
