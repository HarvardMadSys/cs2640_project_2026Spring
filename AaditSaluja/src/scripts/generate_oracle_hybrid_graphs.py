#!/usr/bin/env python3
"""Generate presentation SVGs for the oracle hot/cold hybrid results."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


OUT_DIR = Path("report/figures")


@dataclass(frozen=True)
class Variant:
    label: str
    measured_ops: float
    elapsed_seconds: float
    color: str


OVERALL = [
    Variant("Native", 196.065252, 205.330249, "#2f6fbb"),
    Variant("Hybrid cold-pack", 200.286190, 200.768221, "#3aa675"),
    Variant("Hybrid + prepin", 172.891736, 244.291649, "#d97b29"),
]


STRUCTURAL = [
    {
        "label": "Native",
        "physical_files_created": 7500,
        "materialized_dirs": 65,
        "creates_per_physical_file": 1.0,
        "index_flushes": 0,
        "packed_fraction_pct": 0.0,
        "color": "#2f6fbb",
    },
    {
        "label": "Hybrid cold-pack",
        "physical_files_created": 3127,
        "materialized_dirs": 7,
        "creates_per_physical_file": 7500 / 3127,
        "index_flushes": 18,
        "packed_fraction_pct": 100.0 * (4375 / 7500),
        "color": "#3aa675",
    },
    {
        "label": "Hybrid + prepin",
        "physical_files_created": 3127,
        "materialized_dirs": 7,
        "creates_per_physical_file": 7500 / 3127,
        "index_flushes": 138,
        "packed_fraction_pct": 100.0 * (4375 / 7500),
        "color": "#d97b29",
    },
]


PHASES = [
    {
        "label": "Bulk create",
        "native_ops": 837.108,
        "hybrid_ops": 1484.504,
        "native_p95": 5.809438,
        "hybrid_p95": 7.287187,
    },
    {
        "label": "Hot stat",
        "native_ops": 4798.563,
        "hybrid_ops": 4790.324,
        "native_p95": 2.445593,
        "hybrid_p95": 2.412114,
    },
    {
        "label": "Hot read",
        "native_ops": 2823.204,
        "hybrid_ops": 2168.958,
        "native_p95": 3.940176,
        "hybrid_p95": 3.887430,
    },
    {
        "label": "Hot churn create",
        "native_ops": 35.414,
        "hybrid_ops": 28.401,
        "native_p95": 275.530199,
        "hybrid_p95": 310.386400,
    },
    {
        "label": "Hot churn delete",
        "native_ops": 27.143,
        "hybrid_ops": 29.654,
        "native_p95": 409.113447,
        "hybrid_p95": 507.392268,
    },
    {
        "label": "Cleanup delete",
        "native_ops": 170.047,
        "hybrid_ops": 285.129,
        "native_p95": 4.148610,
        "hybrid_p95": 145.151768,
    },
]


def svg_header(width: int, height: int) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
    ]


def write_svg(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines + ["</svg>"]) + "\n")


def overall_chart() -> None:
    width = 820
    height = 680
    lines = svg_header(width, height)
    lines.append('<text x="92" y="38" font-family="Arial" font-size="24" font-weight="700" fill="#222">Oracle workload: overall measured throughput</text>')

    chart_left = 92
    chart_right = 730
    chart_top = 90
    chart_bottom = 560
    max_ops = 220.0
    bar_width = 115
    gap = 65

    for tick in range(0, 221, 55):
        y = chart_bottom - (tick / max_ops) * (chart_bottom - chart_top)
        lines.append(f'<line x1="{chart_left}" x2="{chart_right}" y1="{y:.1f}" y2="{y:.1f}" stroke="#e6e6e6"/>')
        lines.append(
            f'<text x="{chart_left - 12}" y="{y + 5:.1f}" font-family="Arial" font-size="13" text-anchor="end" fill="#555">{tick}</text>'
        )
    lines.append(f'<line x1="{chart_left}" x2="{chart_left}" y1="{chart_top}" y2="{chart_bottom}" stroke="#333"/>')
    lines.append(f'<line x1="{chart_left}" x2="{chart_right}" y1="{chart_bottom}" y2="{chart_bottom}" stroke="#333"/>')
    lines.append(
        f'<text x="26" y="{(chart_top + chart_bottom) / 2:.1f}" transform="rotate(-90 26 {(chart_top + chart_bottom) / 2:.1f})" font-family="Arial" font-size="15" fill="#333" text-anchor="middle">Measured ops/sec</text>'
    )

    x = chart_left + 45
    for variant in OVERALL:
        bar_height = (variant.measured_ops / max_ops) * (chart_bottom - chart_top)
        y = chart_bottom - bar_height
        lines.append(
            f'<rect x="{x}" y="{y:.1f}" width="{bar_width}" height="{bar_height:.1f}" rx="8" fill="{variant.color}"/>'
        )
        lines.append(
            f'<text x="{x + bar_width / 2:.1f}" y="{y - 8:.1f}" font-family="Arial" font-size="16" font-weight="700" text-anchor="middle" fill="#222">{variant.measured_ops:.1f}</text>'
        )
        lines.append(
            f'<text x="{x + bar_width / 2:.1f}" y="{chart_bottom + 26}" font-family="Arial" font-size="15" text-anchor="middle" fill="#222">{variant.label}</text>'
        )
        x += bar_width + gap
    write_svg(OUT_DIR / "oracle_hybrid_overall_ops.svg", lines)


def grouped_phase_chart(filename: str, title: str, subtitle: str, key_native: str, key_hybrid: str, axis_label: str, value_fmt: str) -> None:
    width = 1320
    height = 760
    lines = svg_header(width, height)
    lines.append(f'<text x="80" y="38" font-family="Arial" font-size="24" font-weight="700" fill="#222">{title}</text>')

    chart_left = 80
    chart_right = 1180
    chart_top = 90
    chart_bottom = 590
    max_value = max(max(item[key_native], item[key_hybrid]) for item in PHASES) * 1.10

    for fraction in range(0, 6):
        tick = max_value * fraction / 5.0
        y = chart_bottom - (tick / max_value) * (chart_bottom - chart_top)
        label = f"{tick:.0f}" if max_value >= 20 else f"{tick:.1f}"
        lines.append(f'<line x1="{chart_left}" x2="{chart_right}" y1="{y:.1f}" y2="{y:.1f}" stroke="#e6e6e6"/>')
        lines.append(
            f'<text x="{chart_left - 12}" y="{y + 5:.1f}" font-family="Arial" font-size="13" text-anchor="end" fill="#555">{label}</text>'
        )

    lines.append(f'<line x1="{chart_left}" x2="{chart_left}" y1="{chart_top}" y2="{chart_bottom}" stroke="#333"/>')
    lines.append(f'<line x1="{chart_left}" x2="{chart_right}" y1="{chart_bottom}" y2="{chart_bottom}" stroke="#333"/>')
    lines.append(
        f'<text x="28" y="{(chart_top + chart_bottom) / 2:.1f}" transform="rotate(-90 28 {(chart_top + chart_bottom) / 2:.1f})" font-family="Arial" font-size="15" fill="#333" text-anchor="middle">{axis_label}</text>'
    )

    group_width = 150
    bar_width = 40
    gap = 18
    start_x = chart_left + 48
    group_gap = 26

    for index, item in enumerate(PHASES):
        x = start_x + index * (group_width + group_gap)
        base_x = x + 18
        values = [(item[key_native], "#2f6fbb"), (item[key_hybrid], "#3aa675")]
        for bar_index, (value, color) in enumerate(values):
            bar_x = base_x + bar_index * (bar_width + gap)
            bar_height = (value / max_value) * (chart_bottom - chart_top)
            y = chart_bottom - bar_height
            lines.append(
                f'<rect x="{bar_x:.1f}" y="{y:.1f}" width="{bar_width}" height="{bar_height:.1f}" rx="6" fill="{color}"/>'
            )
            lines.append(
                f'<text x="{bar_x + bar_width / 2:.1f}" y="{y - 8:.1f}" font-family="Arial" font-size="12" text-anchor="middle" fill="#222">{value_fmt.format(value)}</text>'
            )
        lines.append(
            f'<text x="{x + group_width / 2:.1f}" y="{chart_bottom + 28}" font-family="Arial" font-size="13" text-anchor="middle" fill="#222">{item["label"]}</text>'
        )

    legend_x = 970
    legend_y = 108
    lines.append(f'<rect x="{legend_x}" y="{legend_y}" width="16" height="16" fill="#2f6fbb"/>')
    lines.append(f'<text x="{legend_x + 24}" y="{legend_y + 13}" font-family="Arial" font-size="14" fill="#333">Native</text>')
    lines.append(f'<rect x="{legend_x + 120}" y="{legend_y}" width="16" height="16" fill="#3aa675"/>')
    lines.append(f'<text x="{legend_x + 144}" y="{legend_y + 13}" font-family="Arial" font-size="14" fill="#333">Hybrid cold-pack</text>')

    write_svg(OUT_DIR / filename, lines)


def structural_chart() -> None:
    width = 1380
    height = 920
    lines = svg_header(width, height)
    lines.append('<text x="72" y="40" font-family="Arial" font-size="24" font-weight="700" fill="#222">Oracle workload: structural metrics</text>')
    legend_x = 980
    legend_y = 20
    for offset, item in enumerate(STRUCTURAL):
        x = legend_x + (offset * 118)
        lines.append(f'<rect x="{x}" y="{legend_y}" width="16" height="16" fill="{item["color"]}"/>')
        lines.append(f'<text x="{x + 24}" y="{legend_y + 13}" font-family="Arial" font-size="14" fill="#333">{item["label"]}</text>')

    cards = [
        ("Physical files created", "physical_files_created", "{:.0f}", 8000, 72, 96, 598, 250),
        ("Materialized directories", "materialized_dirs", "{:.0f}", 70, 710, 96, 598, 250),
        ("Logical creates per physical file", "creates_per_physical_file", "{:.2f}", 3.0, 72, 388, 598, 250),
        ("Index flushes", "index_flushes", "{:.0f}", 150, 710, 388, 598, 250),
        ("Packed create fraction (%)", "packed_fraction_pct", "{:.1f}", 100.0, 72, 680, 1236, 176),
    ]

    for title, key, fmt, max_value, x, y, card_width, card_height in cards:
        lines.append(f'<rect x="{x}" y="{y}" width="{card_width}" height="{card_height}" rx="18" fill="#f8fafc" stroke="#d8e0eb"/>')
        lines.append(f'<text x="{x + 22}" y="{y + 32}" font-family="Arial" font-size="18" font-weight="700" fill="#222">{title}</text>')
        chart_left = x + 38
        chart_right = x + card_width - 30
        chart_top = y + 58
        chart_bottom = y + card_height - 36
        lines.append(f'<line x1="{chart_left}" x2="{chart_right}" y1="{chart_bottom}" y2="{chart_bottom}" stroke="#dfe6ef"/>')
        bar_width = 98
        gap = 44
        start_x = chart_left + 12
        for index, item in enumerate(STRUCTURAL):
            value = float(item[key])
            raw_height = (value / max_value) * (chart_bottom - chart_top)
            bar_height = max(raw_height, 8.0)
            bar_x = start_x + index * (bar_width + gap)
            bar_y = chart_bottom - bar_height
            lines.append(
                f'<rect x="{bar_x:.1f}" y="{bar_y:.1f}" width="{bar_width}" height="{bar_height:.1f}" rx="10" fill="{item["color"]}"/>'
            )
            lines.append(f'<text x="{bar_x + bar_width/2:.1f}" y="{bar_y - 8:.1f}" font-family="Arial" font-size="13" text-anchor="middle" fill="#222">{fmt.format(value)}</text>')
            label_y = chart_bottom + 22 if card_height < 200 else chart_bottom + 24
            lines.append(f'<text x="{bar_x + bar_width/2:.1f}" y="{label_y:.1f}" font-family="Arial" font-size="13" text-anchor="middle" fill="#222">{item["label"]}</text>')
    write_svg(OUT_DIR / "oracle_hybrid_structural_metrics.svg", lines)


def main() -> int:
    overall_chart()
    grouped_phase_chart(
        "oracle_hybrid_phase_ops.svg",
        "Oracle workload: per-phase throughput",
        "",
        "native_ops",
        "hybrid_ops",
        "Phase ops/sec",
        "{:.0f}",
    )
    grouped_phase_chart(
        "oracle_hybrid_phase_p95.svg",
        "Oracle workload: per-phase p95 latency",
        "",
        "native_p95",
        "hybrid_p95",
        "p95 latency (ms)",
        "{:.1f}",
    )
    structural_chart()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
