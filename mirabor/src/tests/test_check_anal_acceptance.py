#!/usr/bin/env python3
"""
Test suite for scripts/check_anal_acceptance.py (Plan 05-06 Task 2).

Verifies:
- Script exists with #!/usr/bin/env python3 shebang
- Module-level constants present (SEEDS, WORKLOADS, STEMS, REGIMES,
  EXPECTED_FIGURES, EXPECTED_TABLES)
- All 4 check_sc* functions defined (structural AST check)
- Green path: exits 0 when all Phase 5 outputs in place
- Red path: exits 1 with SC-4 diagnostic when winner_per_regime.md hidden
- --compare-dir /nonexistent override: exits 1 with all 4 SC failures

Run:
    python3 tests/test_check_anal_acceptance.py
"""
import importlib.util
import os
import shutil
import subprocess
import sys


SCRIPT = "scripts/check_anal_acceptance.py"
FAILURES = []


def check(label, cond, detail=""):
    if cond:
        print(f"  PASS  {label}")
    else:
        FAILURES.append((label, detail))
        print(f"  FAIL  {label}  -- {detail}")


def test_exists_and_shebang():
    print("\n[Test 1] Script exists with shebang")
    check("script exists", os.path.exists(SCRIPT))
    if not os.path.exists(SCRIPT):
        return
    with open(SCRIPT) as f:
        first = f.readline().strip()
    check("shebang is '#!/usr/bin/env python3'",
          first == "#!/usr/bin/env python3",
          f"got: {first!r}")


def test_constants():
    print("\n[Test 2] Module-level constants present")
    if not os.path.exists(SCRIPT):
        check("constants", False, "script missing")
        return
    spec = importlib.util.spec_from_file_location("cac", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    check("SEEDS == [42, 7, 13, 23, 31]",
          getattr(m, "SEEDS", None) == [42, 7, 13, 23, 31],
          f"got {getattr(m, 'SEEDS', 'MISSING')}")
    check("WORKLOADS == ['congress', 'court']",
          getattr(m, "WORKLOADS", None) == ["congress", "court"],
          f"got {getattr(m, 'WORKLOADS', 'MISSING')}")
    check("STEMS == ['mrc', 'alpha_sensitivity']",
          getattr(m, "STEMS", None) == ["mrc", "alpha_sensitivity"],
          f"got {getattr(m, 'STEMS', 'MISSING')}")
    expected_regimes = ["Small Cache", "High Skew", "Mixed Sizes", "OHW Regime"]
    check(f"REGIMES == {expected_regimes}",
          getattr(m, "REGIMES", None) == expected_regimes,
          f"got {getattr(m, 'REGIMES', 'MISSING')}")
    expected_figs = [
        "compare_mrc_2panel.pdf",
        "compare_policy_delta.pdf",
        "compare_mrc_overlay.pdf",
        "winner_per_regime_bar.pdf",
    ]
    check("EXPECTED_FIGURES has 4 compare figure filenames",
          getattr(m, "EXPECTED_FIGURES", None) == expected_figs,
          f"got {getattr(m, 'EXPECTED_FIGURES', 'MISSING')}")
    expected_tables = [
        "workload_characterization.md",
        "workload_characterization.json",
        "winner_per_regime.md",
        "winner_per_regime.json",
    ]
    check("EXPECTED_TABLES has 4 table filenames",
          getattr(m, "EXPECTED_TABLES", None) == expected_tables,
          f"got {getattr(m, 'EXPECTED_TABLES', 'MISSING')}")


def test_check_fns():
    print("\n[Test 3] All 4 check_sc* functions defined")
    if not os.path.exists(SCRIPT):
        check("check fns", False, "script missing")
        return
    spec = importlib.util.spec_from_file_location("cac", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    for fname in ("check_sc1_outputs", "check_sc2_seeds",
                  "check_sc3_characterization", "check_sc4_regimes"):
        check(f"function {fname} exists", callable(getattr(m, fname, None)))


def test_green_path():
    print("\n[Test 4] Green path: exits 0 when all outputs in place")
    if not os.path.exists(SCRIPT):
        check("green path", False, "script missing")
        return
    result = subprocess.run(
        ["python3", SCRIPT],
        capture_output=True, text=True,
    )
    check(f"exit code 0 (got {result.returncode})",
          result.returncode == 0,
          f"stderr: {result.stderr[:400]}  stdout: {result.stdout[:400]}")
    check("stdout contains 'PASS'",
          "PASS" in result.stdout,
          f"stdout: {result.stdout[:400]}")


def test_red_path_missing_regime_md():
    print("\n[Test 5] Red path: hiding winner_per_regime.md causes SC-4 failure")
    if not os.path.exists(SCRIPT):
        check("red path", False, "script missing")
        return
    src = "results/compare/winner_per_regime.md"
    dst = "/tmp/_test_wpr_hidden.md"
    if not os.path.exists(src):
        check("red path setup", False, f"{src} not present for test")
        return
    shutil.move(src, dst)
    try:
        result = subprocess.run(
            ["python3", SCRIPT],
            capture_output=True, text=True,
        )
        check(f"exit code 1 (got {result.returncode})",
              result.returncode == 1,
              f"stdout: {result.stdout}")
        check("diagnostic mentions SC-4 or the missing file",
              ("SC-4" in result.stdout or "winner_per_regime" in result.stdout),
              f"stdout: {result.stdout}")
    finally:
        shutil.move(dst, src)

    # Verify restoration works
    result = subprocess.run(
        ["python3", SCRIPT],
        capture_output=True, text=True,
    )
    check(f"after restore: exit 0 (got {result.returncode})",
          result.returncode == 0,
          f"stderr: {result.stderr[:400]}")


def test_nonexistent_compare_dir():
    print("\n[Test 6] --compare-dir /nonexistent: exits 1 with all 4 SC failures")
    if not os.path.exists(SCRIPT):
        check("nonexistent dir", False, "script missing")
        return
    result = subprocess.run(
        ["python3", SCRIPT, "--compare-dir", "/nonexistent_xyz"],
        capture_output=True, text=True,
    )
    check(f"exit code 1 (got {result.returncode})",
          result.returncode == 1,
          f"stdout: {result.stdout[:400]}")
    for sc in ("SC-1", "SC-2", "SC-3", "SC-4"):
        check(f"stdout mentions {sc} FAIL",
              (sc in result.stdout and "FAIL" in result.stdout),
              f"stdout: {result.stdout}")


def main():
    print("=== Test suite for scripts/check_anal_acceptance.py ===")
    test_exists_and_shebang()
    test_constants()
    test_check_fns()
    test_green_path()
    test_red_path_missing_regime_md()
    test_nonexistent_compare_dir()
    print(f"\n=== Result: {len(FAILURES)} failure(s) ===")
    if FAILURES:
        for lbl, det in FAILURES:
            print(f"  FAIL {lbl}: {det}")
        return 1
    print("All tests PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
