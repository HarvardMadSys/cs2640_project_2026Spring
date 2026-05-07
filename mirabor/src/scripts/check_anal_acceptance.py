#!/usr/bin/env python3
"""
Phase 5 (ANAL-01..04) acceptance checker.

Asserts all four ROADMAP Phase 5 success criteria (D-10) are structurally met:

  SC-1: scripts/compare_workloads.py exists and produced the full set of
        outputs under results/compare/ (4 figures + 4 table files).
  SC-2: All 5 seed CSVs exist under results/compare/multiseed/{congress,court}/
        for both mrc and alpha_sensitivity stems (5 × 2 × 2 = 20 files).
  SC-3: results/compare/workload_characterization.md has both workloads'
        rows populated (substring check for 'Congress' and 'Court').
  SC-4: results/compare/winner_per_regime.md has all 4 regime labels.

Exit 0 = all four SC structurally verified; exit 1 with per-SC diagnostic
otherwise. Pattern mirrors scripts/check_wtlfu_acceptance.py (Phase 2 precedent).

Usage:
    python3 scripts/check_anal_acceptance.py [--compare-dir results/compare]
"""
import argparse
import os
import sys

# Grep-discoverable constants — mirror check_wtlfu_acceptance.py pattern.
SEEDS = [42, 7, 13, 23, 31]                                  # D-05
WORKLOADS = ["congress", "court"]
STEMS = ["mrc", "alpha_sensitivity"]
REGIMES = ["Small Cache", "High Skew", "Mixed Sizes", "OHW Regime"]  # D-01
EXPECTED_FIGURES = [
    "compare_mrc_2panel.pdf",
    "compare_policy_delta.pdf",
    "compare_mrc_overlay.pdf",
    "winner_per_regime_bar.pdf",
]
EXPECTED_TABLES = [
    "workload_characterization.md",
    "workload_characterization.json",
    "winner_per_regime.md",
    "winner_per_regime.json",
]


def check_sc1_outputs(compare_dir):
    """SC-1: compare_workloads.py exists and outputs populate results/compare/."""
    fails = []
    if not os.path.exists("scripts/compare_workloads.py"):
        fails.append("scripts/compare_workloads.py not found")
    for fname in EXPECTED_FIGURES:
        p = os.path.join(compare_dir, "figures", fname)
        if not os.path.exists(p):
            fails.append(f"Missing figure: {p}")
    for fname in EXPECTED_TABLES:
        p = os.path.join(compare_dir, fname)
        if not os.path.exists(p):
            fails.append(f"Missing table: {p}")
    return fails


def check_sc2_seeds(compare_dir):
    """SC-2: 20 per-seed CSVs present (5 seeds × 2 workloads × 2 stems)."""
    fails = []
    for wl in WORKLOADS:
        for stem in STEMS:
            for seed in SEEDS:
                p = os.path.join(compare_dir, "multiseed", wl,
                                 f"{stem}_seed{seed}.csv")
                if not os.path.exists(p):
                    fails.append(f"Missing seed file: {p}")
    return fails


def check_sc3_characterization(compare_dir):
    """SC-3: workload_characterization.md has both workloads populated."""
    fails = []
    p = os.path.join(compare_dir, "workload_characterization.md")
    if not os.path.exists(p):
        fails.append(f"Missing: {p}")
        return fails
    with open(p) as f:
        content = f.read()
    if "Congress" not in content:
        fails.append(f"{p}: 'Congress' column/row missing")
    if "Court" not in content:
        fails.append(f"{p}: 'Court' column/row missing")
    # Require enough pipe characters to prove a full 10-row table rendered
    # (10 rows × 4 pipes = 40+ minimum; header + separator + 10 data rows).
    pipe_count = content.count("|")
    if pipe_count < 40:
        fails.append(f"{p}: too few table pipes ({pipe_count} < 40)")
    return fails


def check_sc4_regimes(compare_dir):
    """SC-4: winner_per_regime.md has all 4 regimes represented."""
    fails = []
    p = os.path.join(compare_dir, "winner_per_regime.md")
    if not os.path.exists(p):
        fails.append(f"Missing: {p}")
        return fails
    with open(p) as f:
        content = f.read()
    for regime in REGIMES:
        if regime not in content:
            fails.append(f"{p}: regime label '{regime}' not found")
    return fails


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--compare-dir", default="results/compare",
                    help="Base compare dir (default: results/compare)")
    args = ap.parse_args()

    sc1 = check_sc1_outputs(args.compare_dir)
    sc2 = check_sc2_seeds(args.compare_dir)
    sc3 = check_sc3_characterization(args.compare_dir)
    sc4 = check_sc4_regimes(args.compare_dir)

    print("=== Phase 5 ANAL Acceptance Check ===")
    for label, fails in [
        ("SC-1 (compare outputs present)", sc1),
        ("SC-2 (20 per-seed CSVs present)", sc2),
        ("SC-3 (characterization table populated)", sc3),
        ("SC-4 (4 regimes in winner table)", sc4),
    ]:
        print(f"{label}: {'PASS' if not fails else 'FAIL'}")
        for f in fails:
            print(f"   - {f}")

    total = len(sc1) + len(sc2) + len(sc3) + len(sc4)
    if total > 0:
        print(f"\nFAIL: {total} condition violation(s).")
        return 1
    print("\nPASS: all Phase 5 ANAL conditions satisfied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
