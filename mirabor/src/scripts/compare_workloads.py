#!/usr/bin/env python3
"""
Cross-workload aggregation: load multi-seed CSVs from Plan 05-03, compute
mean/std/p_value/significant per (group, policy) cell, write aggregated CSVs
for downstream plots (Plan 05-05) and regime tables (Plan 05-06).

Reads:
    results/compare/multiseed/{congress,court}/{mrc,alpha_sensitivity}_seed{42,7,13,23,31}.csv

Writes:
    results/compare/aggregated/{congress,court}/mrc_aggregated.csv
    results/compare/aggregated/{congress,court}/alpha_sensitivity_aggregated.csv

Aggregation semantics (D-02):
    - Group by (cache_frac, policy) for MRC; (alpha, policy) for alpha-sensitivity.
    - mean = miss_ratio.mean() across 5 seeds; std = miss_ratio.std(ddof=1).
    - p_value = scipy.stats.ttest_ind(policy_vals, lru_vals, equal_var=False) — Welch's.
    - significant = (p_value < 0.05); LRU rows get p_value=NaN, significant=True.

Usage:
    python3 scripts/compare_workloads.py [--compare-dir results/compare]
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats

# Module-level constants — grep-discoverable per Phase 2 check_wtlfu_acceptance.py pattern.
SEEDS = [42, 7, 13, 23, 31]                  # D-05: 5-seed CI
REFERENCE_CACHE_FRAC = 0.01                  # D-01 OHW-regime cell
HIGH_SKEW_ALPHAS = [1.0, 1.1, 1.2]           # D-01 high-skew regime
SMALL_CACHE_FRAC = 0.001                     # D-01 small-cache regime
P_SIG = 0.05                                 # D-02 Welch's t-test significance threshold
WORKLOADS = ["congress", "court"]
STEMS = ["mrc", "alpha_sensitivity"]
LRU_KEY = "LRU"

# Plan 05-06: regime analysis restricted to the 6 base policies (per D-01 — ablation
# variants have their own figures in Phase 4 and are not part of the regime story).
BASE_POLICIES = ["LRU", "FIFO", "CLOCK", "S3-FIFO", "SIEVE", "W-TinyLFU"]

# Plan 05-06: regime constants (aliases of existing constants for grep-discoverability
# by acceptance gates and downstream readers).
OHW_CACHE_FRAC = REFERENCE_CACHE_FRAC  # 0.01 — canonical 1% cell per D-01


def load_multiseed(multiseed_dir, workload, stem):
    """Load all 5 per-seed CSVs for one (workload, stem) cell; return merged DataFrame.

    Missing seeds print a warning and are skipped (n < 5 at downstream aggregation);
    the `n` column in the aggregated CSV exposes the actual count.
    """
    dfs = []
    for seed in SEEDS:
        path = os.path.join(multiseed_dir, workload, f"{stem}_seed{seed}.csv")
        if not os.path.exists(path):
            print(f"  Warning: {path} not found — skipping seed {seed}", file=sys.stderr)
            continue
        df = pd.read_csv(path)
        df["seed"] = seed
        dfs.append(df)
    if not dfs:
        return None
    return pd.concat(dfs, ignore_index=True)


def aggregate(df, group_cols, pass_through_cols=()):
    """Aggregate per-seed values into mean/std/p_value/significant per (group, policy).

    Args:
        df: merged DataFrame with seed column added by load_multiseed.
        group_cols: list of column names to group by (e.g., ['cache_frac'] or ['alpha']).
        pass_through_cols: extra columns to preserve via mean() (e.g., 'cache_size_bytes').

    Returns:
        DataFrame with columns: *group_cols, *pass_through_cols, policy, mean, std, n,
        p_value, significant — sorted by (*group_cols, policy) for reproducibility.
    """
    rows = []
    for key_vals, group_df in df.groupby(group_cols):
        # Normalize key_vals to a tuple even when groupby single-column.
        if not isinstance(key_vals, tuple):
            key_vals = (key_vals,)
        # LRU reference values for Welch's t-test.
        lru_vals = group_df.loc[group_df["policy"] == LRU_KEY, "miss_ratio"].values
        if len(lru_vals) < 2:
            # Cannot run t-test without ≥ 2 LRU samples. Falls through to NaN p-values.
            lru_vals = None

        for policy, pol_df in group_df.groupby("policy"):
            vals = pol_df["miss_ratio"].values
            n = len(vals)
            mean_v = float(vals.mean()) if n > 0 else float("nan")
            std_v = float(vals.std(ddof=1)) if n > 1 else float("nan")

            # Welch's t-test vs LRU per D-02.
            if policy == LRU_KEY or lru_vals is None or n < 2:
                p_value = float("nan")
                # LRU is the reference, so 'significant' is True by definition
                # (no comparison made). For non-LRU with n<2, also True — can't test.
                significant = True
            else:
                try:
                    _, p_value = stats.ttest_ind(vals, lru_vals, equal_var=False)
                    # scipy returns NaN when both samples have zero variance (T-05-04-02).
                    if np.isnan(p_value):
                        significant = False
                    else:
                        significant = bool(p_value < P_SIG)
                except (ValueError, ZeroDivisionError):
                    p_value = float("nan")
                    significant = False

            row = dict(zip(group_cols, key_vals))
            for pt in pass_through_cols:
                if pt in pol_df.columns:
                    row[pt] = float(pol_df[pt].mean())
            row["policy"] = policy
            row["mean"] = mean_v
            row["std"] = std_v
            row["n"] = n
            row["p_value"] = p_value
            row["significant"] = significant
            rows.append(row)

    agg = pd.DataFrame(rows)
    if agg.empty:
        return agg
    # Deterministic sort for reproducibility (T-05-04-04).
    sort_cols = list(group_cols) + ["policy"]
    return agg.sort_values(sort_cols).reset_index(drop=True)


def write_aggregated(agg_df, out_path):
    """Write aggregated DataFrame with os.makedirs + to_csv pattern
    (matches scripts/workload_stats_json.py line 120-123 idiom)."""
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    agg_df.to_csv(out_path, index=False)
    print(f"  Wrote {out_path} ({len(agg_df)} rows)")


# ---------------------------------------------------------------------------
# Plan 05-06 extensions: ANAL-03 characterization + ANAL-04 regime tables.
# ---------------------------------------------------------------------------


def md_table(headers, rows):
    """Render a GFM markdown table from headers list and rows (list of cell-lists)."""
    header_line = "| " + " | ".join(str(h) for h in headers) + " |"
    separator = "|" + "|".join("---" for _ in headers) + "|"
    body = "\n".join(
        "| " + " | ".join(str(c) for c in row) + " |" for row in rows
    )
    return header_line + "\n" + separator + "\n" + body


def build_workload_characterization(congress_stats_path, court_stats_path):
    """Assemble ANAL-03 side-by-side characterization data from both
    workload_stats.json files. Returns (markdown_str, json_dict, row_labels).
    Returns (None, None, None) if either file is missing."""
    if not (os.path.exists(congress_stats_path) and os.path.exists(court_stats_path)):
        return None, None, None
    with open(congress_stats_path) as f:
        cong = json.load(f)
    with open(court_stats_path) as f:
        crt = json.load(f)

    def _fmt_int(v):
        try:
            return f"{int(v):,}"
        except (TypeError, ValueError):
            return "—"

    def _fmt_float3(v):
        try:
            return f"{float(v):.3f}"
        except (TypeError, ValueError):
            return "—"

    def _fmt_float1(v):
        try:
            return f"{float(v):.1f}"
        except (TypeError, ValueError):
            return "—"

    def _fmt_str(v):
        return str(v) if v is not None else "—"

    # D-08 locked row order (see 05-06 interfaces block for canonical list).
    row_spec = [
        ("Trace path",            "trace_path",         _fmt_str),
        ("Total requests",        "total_requests",     _fmt_int),
        ("Unique objects",        "unique_objects",     _fmt_int),
        ("Zipf α (MLE)",          "alpha_mle",          _fmt_float3),
        ("OHW ratio (10% win)",   "ohw_ratio",          _fmt_float3),
        ("Mean size (bytes)",     "mean_size",          _fmt_float1),
        ("Median size (bytes)",   "median_size",        _fmt_int),
        ("p95 size (bytes)",      "p95_size",           _fmt_int),
        ("Max size (bytes)",      "max_size",           _fmt_int),
        ("Working set (bytes)",   "working_set_bytes",  _fmt_int),
    ]

    rows = []
    for label, key, fmt in row_spec:
        rows.append([label, fmt(cong.get(key)), fmt(crt.get(key))])

    md = "# Workload Characterization (ANAL-03 / D-08)\n\n"
    md += ("Generated by scripts/compare_workloads.py from "
           "results/{congress,court}/workload_stats.json.\n\n")
    md += md_table(["Metric", "Congress", "Court"], rows)
    md += "\n"

    data = {"congress": cong, "court": crt}
    return md, data, [r[0] for r in row_spec]


def _winner_in_group(df, value_col="mean"):
    """Return (winner_policy, winner_value) for the row with argmin value_col,
    restricted to BASE_POLICIES. Returns (None, nan) when df is empty."""
    sub = df[df["policy"].isin(BASE_POLICIES)]
    if sub.empty:
        return (None, float("nan"))
    per_policy = sub.groupby("policy")[value_col].mean()
    winner = per_policy.idxmin()
    return (winner, float(per_policy.min()))


def build_winner_per_regime(compare_dir):
    """Assemble ANAL-04 4-regime × 2-workload winner data. Returns
    (markdown_str, json_list). Regimes defined per D-01."""
    agg_dir = os.path.join(compare_dir, "aggregated")
    mrc_cong_path = os.path.join(agg_dir, "congress", "mrc_aggregated.csv")
    mrc_crt_path = os.path.join(agg_dir, "court", "mrc_aggregated.csv")
    alpha_cong_path = os.path.join(agg_dir, "congress", "alpha_sensitivity_aggregated.csv")
    alpha_crt_path = os.path.join(agg_dir, "court", "alpha_sensitivity_aggregated.csv")

    paths = [mrc_cong_path, mrc_crt_path, alpha_cong_path, alpha_crt_path]
    if any(not os.path.exists(p) for p in paths):
        missing = [p for p in paths if not os.path.exists(p)]
        print(f"  build_winner_per_regime: missing {missing}", file=sys.stderr)
        return None, None

    mrc_cong = pd.read_csv(mrc_cong_path)
    mrc_crt = pd.read_csv(mrc_crt_path)
    alpha_cong = pd.read_csv(alpha_cong_path)
    alpha_crt = pd.read_csv(alpha_crt_path)

    # Court single-seed mrc.csv for Mixed Sizes regime (byte_miss_ratio).
    court_single_path = os.path.join("results", "court", "mrc.csv")
    court_single_df = None
    if os.path.exists(court_single_path):
        court_single_df = pd.read_csv(court_single_path)

    regimes = []

    # Small Cache (cache_frac=0.001)
    cw, cv = _winner_in_group(
        mrc_cong[abs(mrc_cong["cache_frac"] - SMALL_CACHE_FRAC) < 1e-9])
    kw, kv = _winner_in_group(
        mrc_crt[abs(mrc_crt["cache_frac"] - SMALL_CACHE_FRAC) < 1e-9])
    regimes.append({
        "regime": "Small Cache",
        "detail": f"cache_frac={SMALL_CACHE_FRAC}",
        "congress_winner": cw, "congress_miss": cv,
        "court_winner": kw, "court_miss": kv,
    })

    # High Skew (α ∈ HIGH_SKEW_ALPHAS, winner by mean-across-alphas)
    cw, cv = _winner_in_group(
        alpha_cong[alpha_cong["alpha"].round(2).isin(
            [round(a, 2) for a in HIGH_SKEW_ALPHAS])])
    kw, kv = _winner_in_group(
        alpha_crt[alpha_crt["alpha"].round(2).isin(
            [round(a, 2) for a in HIGH_SKEW_ALPHAS])])
    regimes.append({
        "regime": "High Skew",
        "detail": f"α ∈ {{{', '.join(str(a) for a in HIGH_SKEW_ALPHAS)}}}",
        "congress_winner": cw, "congress_miss": cv,
        "court_winner": kw, "court_miss": kv,
    })

    # Mixed Sizes (Court byte-MRC at 1%; Congress N/A per D-01 — uniform sizes)
    mixed_kw, mixed_kv = (None, float("nan"))
    if court_single_df is not None:
        court_1pct = court_single_df[
            abs(court_single_df["cache_frac"] - OHW_CACHE_FRAC) < 1e-9
        ]
        court_1pct_base = court_1pct[court_1pct["policy"].isin(BASE_POLICIES)]
        if not court_1pct_base.empty:
            best_idx = court_1pct_base["byte_miss_ratio"].idxmin()
            mixed_kw = court_1pct_base.loc[best_idx, "policy"]
            mixed_kv = float(court_1pct_base.loc[best_idx, "byte_miss_ratio"])
    regimes.append({
        "regime": "Mixed Sizes",
        "detail": f"Court byte-MRC at cache_frac={OHW_CACHE_FRAC} (single-seed)",
        "congress_winner": None, "congress_miss": float("nan"),
        "court_winner": mixed_kw, "court_miss": mixed_kv,
    })

    # OHW Regime (cache_frac=0.01)
    cw, cv = _winner_in_group(
        mrc_cong[abs(mrc_cong["cache_frac"] - OHW_CACHE_FRAC) < 1e-9])
    kw, kv = _winner_in_group(
        mrc_crt[abs(mrc_crt["cache_frac"] - OHW_CACHE_FRAC) < 1e-9])
    regimes.append({
        "regime": "OHW Regime",
        "detail": f"cache_frac={OHW_CACHE_FRAC}",
        "congress_winner": cw, "congress_miss": cv,
        "court_winner": kw, "court_miss": kv,
    })

    # Render markdown.
    rows = []
    for r in regimes:
        cw_str = r["congress_winner"] if r["congress_winner"] is not None else "N/A"
        cv_str = (f"{r['congress_miss']:.3f}"
                  if not np.isnan(r["congress_miss"]) else "–")
        kw_str = r["court_winner"] if r["court_winner"] is not None else "N/A"
        kv_str = (f"{r['court_miss']:.3f}"
                  if not np.isnan(r["court_miss"]) else "–")
        rows.append([
            f"{r['regime']} ({r['detail']})",
            cw_str, cv_str, kw_str, kv_str,
        ])

    md = "# Winner per Regime (ANAL-04 / D-01)\n\n"
    md += ("Generated by scripts/compare_workloads.py. Regime definitions per D-01 in\n"
           "`.planning/phases/05-cross-workload-analysis-infrastructure/05-CONTEXT.md`.\n"
           "Winner column values are 5-seed mean miss_ratio (or single-seed "
           "byte_miss_ratio\n"
           "for the Mixed Sizes regime — multi-seed byte-MRC aggregation deferred "
           "to v2).\n\n")
    md += md_table(
        ["Regime", "Congress Winner", "Congress Miss", "Court Winner", "Court Miss"],
        rows,
    )
    md += "\n"

    # Render JSON (convert nan → None for JSON serialization).
    json_safe = []
    for r in regimes:
        jr = dict(r)
        for k in ("congress_miss", "court_miss"):
            v = jr[k]
            if isinstance(v, float) and np.isnan(v):
                jr[k] = None
        json_safe.append(jr)

    return md, json_safe


def write_table_artifacts(compare_dir, congress_dir, court_dir):
    """Emit ANAL-03 + ANAL-04 markdown + JSON tables.
    Plan 05-06 entry point. Called from main() after the aggregation step.
    """
    # ANAL-03 characterization
    cong_stats = os.path.join(congress_dir, "workload_stats.json")
    court_stats = os.path.join(court_dir, "workload_stats.json")
    md_char, json_char, _ = build_workload_characterization(cong_stats, court_stats)
    if md_char is None:
        missing = [p for p in (cong_stats, court_stats) if not os.path.exists(p)]
        print(f"  Warning: workload_stats.json missing {missing} — "
              f"skipping ANAL-03 table", file=sys.stderr)
    else:
        os.makedirs(compare_dir, exist_ok=True)
        md_path = os.path.join(compare_dir, "workload_characterization.md")
        json_path = os.path.join(compare_dir, "workload_characterization.json")
        with open(md_path, "w") as f:
            f.write(md_char)
        with open(json_path, "w") as f:
            json.dump(json_char, f, indent=2)
        print(f"  Wrote {md_path}")
        print(f"  Wrote {json_path}")

    # ANAL-04 winner per regime
    md_reg, json_reg = build_winner_per_regime(compare_dir)
    if md_reg is None:
        print(f"  Warning: aggregated CSVs missing — skipping ANAL-04 table",
              file=sys.stderr)
    else:
        md_path = os.path.join(compare_dir, "winner_per_regime.md")
        json_path = os.path.join(compare_dir, "winner_per_regime.json")
        with open(md_path, "w") as f:
            f.write(md_reg)
        with open(json_path, "w") as f:
            json.dump(json_reg, f, indent=2)
        print(f"  Wrote {md_path}")
        print(f"  Wrote {json_path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--compare-dir", default="results/compare",
                    help="Base compare/ directory (default: results/compare)")
    ap.add_argument("--congress-dir", default=None,
                    help="Override Congress workload subdir name (default: 'congress')")
    ap.add_argument("--court-dir", default=None,
                    help="Override Court workload subdir name (default: 'court')")
    ap.add_argument("--congress-stats-dir", default=os.path.join("results", "congress"),
                    help="Congress results dir for workload_stats.json lookup "
                         "(Plan 05-06 / ANAL-03)")
    ap.add_argument("--court-stats-dir", default=os.path.join("results", "court"),
                    help="Court results dir for workload_stats.json lookup "
                         "(Plan 05-06 / ANAL-03)")
    args = ap.parse_args()

    multiseed_dir = os.path.join(args.compare_dir, "multiseed")
    aggregated_dir = os.path.join(args.compare_dir, "aggregated")

    if not os.path.isdir(multiseed_dir):
        print(f"Error: {multiseed_dir} not found — run "
              f"scripts/run_multiseed_sweep.py first.", file=sys.stderr)
        return 1

    # Allow per-workload subdir overrides (rare; default is the workload name itself).
    workload_subdirs = {
        "congress": args.congress_dir or "congress",
        "court": args.court_dir or "court",
    }

    print(f"=== Aggregating 5-seed CSVs from {multiseed_dir} ===")

    for workload in WORKLOADS:
        subdir = workload_subdirs[workload]

        # MRC aggregation: group by cache_frac, pass cache_size_bytes through.
        mrc_df = load_multiseed(multiseed_dir, subdir, "mrc")
        if mrc_df is None:
            print(f"  Warning: no MRC CSVs for {workload} — skipping", file=sys.stderr)
        else:
            mrc_agg = aggregate(mrc_df, ["cache_frac"],
                                pass_through_cols=("cache_size_bytes",))
            # Reorder columns to match documented schema.
            mrc_schema = ["cache_frac", "cache_size_bytes", "policy",
                          "mean", "std", "n", "p_value", "significant"]
            mrc_agg = mrc_agg[[c for c in mrc_schema if c in mrc_agg.columns]]
            write_aggregated(mrc_agg,
                             os.path.join(aggregated_dir, workload, "mrc_aggregated.csv"))

        # Alpha-sensitivity aggregation: group by alpha, no pass-through.
        alpha_df = load_multiseed(multiseed_dir, subdir, "alpha_sensitivity")
        if alpha_df is None:
            print(f"  Warning: no alpha_sensitivity CSVs for {workload} — skipping",
                  file=sys.stderr)
        else:
            alpha_agg = aggregate(alpha_df, ["alpha"])
            alpha_schema = ["alpha", "policy", "mean", "std", "n", "p_value", "significant"]
            alpha_agg = alpha_agg[[c for c in alpha_schema if c in alpha_agg.columns]]
            write_aggregated(alpha_agg,
                             os.path.join(aggregated_dir, workload,
                                          "alpha_sensitivity_aggregated.csv"))

    print("=== Aggregation complete. ===")

    # Plan 05-06: emit ANAL-03 characterization + ANAL-04 winner-per-regime tables.
    print("=== Emitting ANAL-03 + ANAL-04 tables ===")
    # Default congress/court dirs match the Phase 3 layout; overridable via CLI.
    write_table_artifacts(
        compare_dir=args.compare_dir,
        congress_dir=args.congress_stats_dir,
        court_dir=args.court_stats_dir,
    )
    print("=== Tables complete. ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
