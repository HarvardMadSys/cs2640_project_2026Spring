#!/usr/bin/env python3
"""
Test suite for scripts/compare_workloads.py (Plan 05-04).

Verifies:
- Module-level constants (SEEDS, P_SIG, REFERENCE_CACHE_FRAC, HIGH_SKEW_ALPHAS, SMALL_CACHE_FRAC)
- scipy.stats.ttest_ind is imported and wired in
- End-to-end run produces 4 aggregated CSVs with locked schemas
- n=5 in every row (all 20 per-seed CSVs ingested)
- LRU rows are reference (p_value NaN, significant True)
- W-TinyLFU is significant vs LRU at high α on Congress (Phase 2 finding)
- Idempotent: two runs produce byte-identical output

Run:
    python3 tests/test_compare_workloads.py
"""
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys

import pandas as pd


SCRIPT = "scripts/compare_workloads.py"
OUT_DIR = "results/compare/aggregated"
EXPECTED_CSVS = [
    "results/compare/aggregated/congress/mrc_aggregated.csv",
    "results/compare/aggregated/congress/alpha_sensitivity_aggregated.csv",
    "results/compare/aggregated/court/mrc_aggregated.csv",
    "results/compare/aggregated/court/alpha_sensitivity_aggregated.csv",
]
MRC_HEADER = "cache_frac,cache_size_bytes,policy,mean,std,n,p_value,significant"
ALPHA_HEADER = "alpha,policy,mean,std,n,p_value,significant"


FAILURES = []


def check(label, cond, detail=""):
    if cond:
        print(f"  PASS  {label}")
    else:
        FAILURES.append((label, detail))
        print(f"  FAIL  {label}  -- {detail}")


def test_module_constants():
    print("\n[Test 1/2] Module importable + constants present")
    check("script exists", os.path.exists(SCRIPT))
    spec = importlib.util.spec_from_file_location("cw", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    check("SEEDS == [42, 7, 13, 23, 31]", getattr(m, "SEEDS", None) == [42, 7, 13, 23, 31],
          f"got {getattr(m, 'SEEDS', 'MISSING')}")
    check("P_SIG == 0.05", getattr(m, "P_SIG", None) == 0.05,
          f"got {getattr(m, 'P_SIG', 'MISSING')}")
    check("REFERENCE_CACHE_FRAC == 0.01",
          getattr(m, "REFERENCE_CACHE_FRAC", None) == 0.01,
          f"got {getattr(m, 'REFERENCE_CACHE_FRAC', 'MISSING')}")
    check("HIGH_SKEW_ALPHAS == [1.0, 1.1, 1.2]",
          getattr(m, "HIGH_SKEW_ALPHAS", None) == [1.0, 1.1, 1.2],
          f"got {getattr(m, 'HIGH_SKEW_ALPHAS', 'MISSING')}")
    check("SMALL_CACHE_FRAC == 0.001",
          getattr(m, "SMALL_CACHE_FRAC", None) == 0.001,
          f"got {getattr(m, 'SMALL_CACHE_FRAC', 'MISSING')}")


def test_scipy_import():
    print("\n[Test 3] scipy.stats.ttest_ind wired")
    with open(SCRIPT) as f:
        src = f.read()
    check("scipy imported",
          "from scipy import stats" in src or "import scipy" in src)
    check("ttest_ind called", "ttest_ind" in src)


def test_end_to_end_run():
    print("\n[Test 4] End-to-end run exits 0 and creates 4 CSVs")
    if os.path.isdir(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    result = subprocess.run(
        ["python3", SCRIPT],
        capture_output=True, text=True,
    )
    check(f"exit code 0 (got {result.returncode})",
          result.returncode == 0,
          f"stderr: {result.stderr[:400]}")
    for p in EXPECTED_CSVS:
        check(f"CSV exists: {p}", os.path.exists(p))


def test_schemas():
    print("\n[Test 5/6] Aggregated CSV headers match locked schema")
    for p in ["results/compare/aggregated/congress/mrc_aggregated.csv",
              "results/compare/aggregated/court/mrc_aggregated.csv"]:
        if not os.path.exists(p):
            check(f"header check {p}", False, "file missing")
            continue
        with open(p) as f:
            header = f.readline().strip()
        check(f"MRC header {p}", header == MRC_HEADER,
              f"got: {header}")
    for p in ["results/compare/aggregated/congress/alpha_sensitivity_aggregated.csv",
              "results/compare/aggregated/court/alpha_sensitivity_aggregated.csv"]:
        if not os.path.exists(p):
            check(f"header check {p}", False, "file missing")
            continue
        with open(p) as f:
            header = f.readline().strip()
        check(f"alpha header {p}", header == ALPHA_HEADER,
              f"got: {header}")


def test_n_equals_5():
    print("\n[Test 7] Every row has n == 5")
    for p in EXPECTED_CSVS:
        if not os.path.exists(p):
            check(f"n=5 check {p}", False, "file missing")
            continue
        df = pd.read_csv(p)
        all5 = (df["n"] == 5).all()
        check(f"n=5 in {p} ({len(df)} rows)",
              all5, f"counts: {df['n'].value_counts().to_dict()}")


def test_lru_is_reference():
    print("\n[Test 8] LRU rows: p_value NaN, significant True")
    for p in EXPECTED_CSVS:
        if not os.path.exists(p):
            continue
        df = pd.read_csv(p)
        lru = df[df["policy"] == "LRU"]
        check(f"{p}: LRU rows exist", not lru.empty)
        check(f"{p}: LRU p_value all NaN",
              lru["p_value"].isna().all(),
              f"non-NaN: {lru[~lru['p_value'].isna()]}")
        # significant column may be bool or string "True"
        sig = lru["significant"].astype(str).str.lower()
        check(f"{p}: LRU significant all True",
              (sig == "true").all(),
              f"sig values: {lru['significant'].unique()}")


def test_wtlfu_significant_high_alpha_congress():
    print("\n[Test 9] W-TinyLFU significant at high α on Congress")
    p = "results/compare/aggregated/congress/alpha_sensitivity_aggregated.csv"
    if not os.path.exists(p):
        check("WTLFU sig", False, "file missing")
        return
    df = pd.read_csv(p)
    hi = df[(df["policy"] == "W-TinyLFU") &
            (df["alpha"].round(2).isin([1.0, 1.1, 1.2]))]
    check(f"WTLFU rows at α∈{{1.0,1.1,1.2}} (got {len(hi)})", len(hi) == 3)
    # significant may be bool or "True"/"False" string
    sig = hi["significant"].astype(str).str.lower()
    check("All 3 WTLFU high-α rows significant",
          (sig == "true").all(),
          f"rows:\n{hi[['alpha', 'mean', 'p_value', 'significant']]}")


def test_mean_sanity_vs_legacy():
    """Test 10: LRU mean across 5 seeds at cache_frac=0.01 should be close to
    legacy seed=42 run (seed=42 IS one of the 5 seeds)."""
    print("\n[Test 10] LRU mean at cache_frac=0.01 close to seed=42 value")
    agg = "results/compare/aggregated/congress/mrc_aggregated.csv"
    seed42 = "results/compare/multiseed/congress/mrc_seed42.csv"
    if not (os.path.exists(agg) and os.path.exists(seed42)):
        check("mean sanity", False, "required file(s) missing")
        return
    a = pd.read_csv(agg)
    s = pd.read_csv(seed42)
    a_lru = a[(a["policy"] == "LRU") & (a["cache_frac"].round(4) == 0.01)]
    s_lru = s[(s["policy"] == "LRU") & (s["cache_frac"].round(4) == 0.01)]
    check("agg LRU @ cache_frac=0.01 exists", not a_lru.empty)
    check("seed42 LRU @ cache_frac=0.01 exists", not s_lru.empty)
    if a_lru.empty or s_lru.empty:
        return
    a_val = float(a_lru["mean"].iloc[0])
    s_val = float(s_lru["miss_ratio"].iloc[0])
    diff = abs(a_val - s_val)
    check(f"|agg_mean - seed42| < 0.02 (agg={a_val:.4f}, seed42={s_val:.4f}, diff={diff:.4f})",
          diff < 0.02)


def hash_csvs():
    h = {}
    for p in EXPECTED_CSVS:
        if os.path.exists(p):
            with open(p, "rb") as f:
                h[p] = hashlib.md5(f.read()).hexdigest()
    return h


def test_idempotent():
    print("\n[Test 11] Idempotent: second run produces byte-identical CSVs")
    first = hash_csvs()
    result = subprocess.run(
        ["python3", SCRIPT],
        capture_output=True, text=True,
    )
    check(f"second run exit 0 (got {result.returncode})", result.returncode == 0,
          f"stderr: {result.stderr[:400]}")
    second = hash_csvs()
    for p in EXPECTED_CSVS:
        check(f"byte-identical: {p}",
              first.get(p) == second.get(p),
              f"first={first.get(p)}, second={second.get(p)}")


def main():
    print("=== Test suite for scripts/compare_workloads.py ===")
    test_module_constants()
    test_scipy_import()
    test_end_to_end_run()
    test_schemas()
    test_n_equals_5()
    test_lru_is_reference()
    test_wtlfu_significant_high_alpha_congress()
    test_mean_sanity_vs_legacy()
    test_idempotent()
    print(f"\n=== Result: {len(FAILURES)} failure(s) ===")
    if FAILURES:
        for lbl, det in FAILURES:
            print(f"  FAIL {lbl}: {det}")
        return 1
    print("All tests PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
