#!/usr/bin/env python3
"""
Test suite for Plan 05-06 extensions to scripts/compare_workloads.py.

Verifies that running the script now emits 4 additional artifacts:
    results/compare/workload_characterization.md
    results/compare/workload_characterization.json
    results/compare/winner_per_regime.md
    results/compare/winner_per_regime.json

Plus the module-level additions for regime analysis:
    BASE_POLICIES, OHW_CACHE_FRAC, md_table,
    build_workload_characterization, build_winner_per_regime, write_table_artifacts

Run:
    python3 tests/test_compare_workloads_tables.py
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys


SCRIPT = "scripts/compare_workloads.py"
COMPARE_DIR = "results/compare"
EXPECTED_TABLES = [
    f"{COMPARE_DIR}/workload_characterization.md",
    f"{COMPARE_DIR}/workload_characterization.json",
    f"{COMPARE_DIR}/winner_per_regime.md",
    f"{COMPARE_DIR}/winner_per_regime.json",
]

FAILURES = []


def check(label, cond, detail=""):
    if cond:
        print(f"  PASS  {label}")
    else:
        FAILURES.append((label, detail))
        print(f"  FAIL  {label}  -- {detail}")


def load_module():
    spec = importlib.util.spec_from_file_location("cw", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_module_new_constants():
    print("\n[Test 1] New module-level constants (BASE_POLICIES, OHW_CACHE_FRAC)")
    m = load_module()
    expected = ["LRU", "FIFO", "CLOCK", "S3-FIFO", "SIEVE", "W-TinyLFU"]
    got = getattr(m, "BASE_POLICIES", None)
    check("BASE_POLICIES == ['LRU','FIFO','CLOCK','S3-FIFO','SIEVE','W-TinyLFU']",
          got == expected, f"got {got}")
    check("OHW_CACHE_FRAC == 0.01",
          getattr(m, "OHW_CACHE_FRAC", None) == 0.01,
          f"got {getattr(m, 'OHW_CACHE_FRAC', 'MISSING')}")


def test_new_functions_defined():
    print("\n[Test 2] New functions defined: md_table, build_workload_characterization, "
          "build_winner_per_regime, write_table_artifacts")
    m = load_module()
    for fname in ("md_table", "build_workload_characterization",
                  "build_winner_per_regime", "write_table_artifacts"):
        check(f"function {fname} exists", callable(getattr(m, fname, None)))


def test_end_to_end_creates_4_tables():
    print("\n[Test 3] End-to-end run emits 4 table files")
    # Clean just the tables (don't delete aggregated CSVs we already have)
    for p in EXPECTED_TABLES:
        if os.path.exists(p):
            os.remove(p)
    result = subprocess.run(
        ["python3", SCRIPT],
        capture_output=True, text=True,
    )
    check(f"exit code 0 (got {result.returncode})",
          result.returncode == 0,
          f"stderr: {result.stderr[:400]}")
    for p in EXPECTED_TABLES:
        exists = os.path.exists(p)
        size_ok = exists and os.path.getsize(p) > 100
        check(f"{p} exists", exists)
        check(f"{p} size > 100 bytes", size_ok)


def test_characterization_content():
    print("\n[Test 4] workload_characterization.md has both workloads populated")
    p = f"{COMPARE_DIR}/workload_characterization.md"
    if not os.path.exists(p):
        check("characterization exists", False, "missing")
        return
    with open(p) as f:
        content = f.read()
    check("contains 'Congress'", "Congress" in content)
    check("contains 'Court'", "Court" in content)
    # Row labels we expect (D-08 order)
    for label in ("Trace path", "Total requests", "Unique objects", "Zipf",
                  "OHW", "Mean size", "Median size", "p95 size",
                  "Max size", "Working set"):
        check(f"row label '{label}' present", label in content)


def test_characterization_json_valid():
    print("\n[Test 5] workload_characterization.json is valid + has both workloads")
    p = f"{COMPARE_DIR}/workload_characterization.json"
    if not os.path.exists(p):
        check("json exists", False, "missing")
        return
    with open(p) as f:
        data = json.load(f)
    check("has top-level 'congress' key",
          isinstance(data, dict) and "congress" in data,
          f"keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
    check("has top-level 'court' key",
          isinstance(data, dict) and "court" in data)
    if "congress" in data:
        for k in ("trace_path", "total_requests", "unique_objects", "alpha_mle",
                  "ohw_ratio", "mean_size", "median_size", "p95_size",
                  "max_size", "working_set_bytes"):
            check(f"congress.{k} present", k in data["congress"])


def test_regime_content():
    print("\n[Test 6] winner_per_regime.md has all 4 regime labels and no ablation contamination")
    p = f"{COMPARE_DIR}/winner_per_regime.md"
    if not os.path.exists(p):
        check("regime md exists", False, "missing")
        return
    with open(p) as f:
        content = f.read()
    for regime in ("Small Cache", "High Skew", "Mixed Sizes", "OHW Regime"):
        check(f"regime label '{regime}' present", regime in content)
    # No ablation variants in winner cells
    for abl in ("S3-FIFO-5", "S3-FIFO-20", "SIEVE-NoProm", "W-TinyLFU+DK"):
        check(f"no ablation '{abl}' in regime table", abl not in content)
    # Mixed Sizes Congress should be N/A
    # Look for "Mixed Sizes" line containing "N/A"
    mixed_lines = [ln for ln in content.splitlines() if "Mixed Sizes" in ln]
    check("Mixed Sizes row has N/A (Congress uniform-sizes per D-01)",
          any("N/A" in ln for ln in mixed_lines),
          f"lines: {mixed_lines}")


def test_regime_json_valid():
    print("\n[Test 7] winner_per_regime.json is valid list of 4 dicts")
    p = f"{COMPARE_DIR}/winner_per_regime.json"
    if not os.path.exists(p):
        check("regime json exists", False, "missing")
        return
    with open(p) as f:
        data = json.load(f)
    check("is a list", isinstance(data, list))
    if isinstance(data, list):
        check("has 4 regime entries", len(data) == 4, f"got {len(data)}")
        for i, r in enumerate(data):
            for k in ("regime", "detail", "congress_winner", "congress_miss",
                      "court_winner", "court_miss"):
                check(f"regime[{i}].{k} present", k in r)


def test_characterization_alpha_matches_json():
    print("\n[Test 8] Rendered α_mle matches JSON source to 2 decimals")
    md_p = f"{COMPARE_DIR}/workload_characterization.md"
    src_p = "results/congress/workload_stats.json"
    if not (os.path.exists(md_p) and os.path.exists(src_p)):
        check("alpha cross-check", False, "required file missing")
        return
    with open(src_p) as f:
        cong = json.load(f)
    with open(md_p) as f:
        content = f.read()
    alpha = cong["alpha_mle"]
    # 2-decimal rendering should appear as substring
    two_dec = f"{alpha:.2f}"
    check(f"α_mle {two_dec} present in markdown",
          two_dec in content, f"α={alpha}, 2dec={two_dec}")


def hash_files():
    h = {}
    for p in EXPECTED_TABLES:
        if os.path.exists(p):
            with open(p, "rb") as f:
                h[p] = hashlib.md5(f.read()).hexdigest()
    return h


def test_idempotent():
    print("\n[Test 9] Re-running produces byte-identical tables")
    first = hash_files()
    result = subprocess.run(
        ["python3", SCRIPT],
        capture_output=True, text=True,
    )
    check(f"second run exit 0 (got {result.returncode})",
          result.returncode == 0,
          f"stderr: {result.stderr[:400]}")
    second = hash_files()
    for p in EXPECTED_TABLES:
        check(f"byte-identical: {p}",
              first.get(p) == second.get(p),
              f"first={first.get(p)}, second={second.get(p)}")


def main():
    print("=== Test suite for Plan 05-06 table extensions ===")
    test_module_new_constants()
    test_new_functions_defined()
    test_end_to_end_creates_4_tables()
    test_characterization_content()
    test_characterization_json_valid()
    test_regime_content()
    test_regime_json_valid()
    test_characterization_alpha_matches_json()
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
