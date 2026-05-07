#!/usr/bin/env python3
"""
Congress focal: 20-seed re-check of LHD-Full vs GDSF (and SIEVE/W-TinyLFU
as context) at cache_frac=0.01, all four alphas. Closes the rigor gap on
the alpha=0.8 cell where the v4 paper reported a 0.5 pp LHD-Full win
within seed-sigma at n=5.

Reports per-policy mean +/- std at n=20 plus Welch t-test on
LHD-Full - GDSF (the borderline pair). Writes summary.json.
"""
import csv
import math
import shutil
import subprocess
import sys
import json
from pathlib import Path
from statistics import mean, stdev

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "cache_sim"
TRACE = ROOT / "traces" / "congress_trace.csv"
OUT_DIR = ROOT / "results" / "congress_lhdfull_high_seed"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SEEDS = list(range(42, 42 + 20))
ALPHAS = [0.6, 0.8, 1.0, 1.2]
CACHE_FRAC = 0.01
POLICIES = ["SIEVE", "W-TinyLFU", "GDSF", "LHD-Full"]
POLICY_FLAG = "sieve,wtinylfu,gdsf,lhd-full"


def run(alpha, seed):
    cell_dir = OUT_DIR / f"_run_a{alpha}_d{seed}"
    cell_dir.mkdir(parents=True, exist_ok=True)
    cmd = [str(SIM),
           "--trace", str(TRACE),
           "--replay-zipf",
           "--alpha", str(alpha),
           "--seed", str(seed),
           "--policies", POLICY_FLAG,
           "--cache-sizes", str(CACHE_FRAC),
           "--output-dir", str(cell_dir)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(f"FAIL a={alpha} s={seed}\n{proc.stderr[-1500:]}\n")
        shutil.rmtree(cell_dir, ignore_errors=True)
        return None
    out = {}
    with open(cell_dir / "mrc.csv") as f:
        for row in csv.DictReader(f):
            out[row["policy"]] = float(row["miss_ratio"])
    shutil.rmtree(cell_dir, ignore_errors=True)
    return out


def welch_t(a, b):
    """Welch's t-test, two-sided. Returns (t, df, p_approx)."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return None, None, None
    ma, mb = mean(a), mean(b)
    va, vb = stdev(a) ** 2, stdev(b) ** 2
    se = math.sqrt(va / na + vb / nb)
    if se == 0:
        return None, None, None
    t = (ma - mb) / se
    df_num = (va / na + vb / nb) ** 2
    df_den = (va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1)
    df = df_num / df_den if df_den > 0 else None
    p = math.erfc(abs(t) / math.sqrt(2))
    return t, df, p


print(f"Congress 20-seed sweep, cache_frac={CACHE_FRAC}, alphas={ALPHAS}")
print(f"Policies: {POLICIES}")
print(f"Seeds: 42..{42 + len(SEEDS) - 1} (n={len(SEEDS)})\n")

results = {}
for a in ALPHAS:
    by_policy = {p: [] for p in POLICIES}
    for s in SEEDS:
        d = run(a, s)
        if d is None:
            continue
        for p in POLICIES:
            if p in d:
                by_policy[p].append(d[p])

    if any(len(by_policy[p]) < 2 for p in POLICIES):
        print(f"  alpha={a}: insufficient seeds, skipping")
        continue

    # Welch on LHD-Full vs GDSF (the borderline pair the paper flagged)
    t_lg, df_lg, p_lg = welch_t(by_policy["LHD-Full"], by_policy["GDSF"])
    gap_lg = (mean(by_policy["LHD-Full"]) - mean(by_policy["GDSF"])) * 100

    cell = {"alpha": a, "n_seeds": len(by_policy["GDSF"])}
    for p in POLICIES:
        cell[f"{p}_mean"] = round(mean(by_policy[p]), 5)
        cell[f"{p}_std"] = round(stdev(by_policy[p]), 5)
    cell["lhd_minus_gdsf_pp"] = round(gap_lg, 3)
    cell["welch_t_lhd_vs_gdsf"] = round(t_lg, 3) if t_lg is not None else None
    cell["welch_df_lhd_vs_gdsf"] = round(df_lg, 1) if df_lg is not None else None
    cell["welch_p_lhd_vs_gdsf"] = round(p_lg, 4) if p_lg is not None else None
    results[a] = cell

    print(f"  alpha={a}:")
    for p in POLICIES:
        m_pct = mean(by_policy[p]) * 100
        s_pct = stdev(by_policy[p]) * 100
        print(f"    {p:>10}: {m_pct:6.2f} +/- {s_pct:4.2f} pp")
    print(f"    LHD-Full - GDSF gap: {gap_lg:+.2f} pp,  Welch t={t_lg:+.2f}, "
          f"df={df_lg:.1f}, p={p_lg:.4f}\n")

with open(OUT_DIR / "summary.json", "w") as f:
    json.dump(results, f, indent=2)
print(f"Wrote {OUT_DIR / 'summary.json'}")

print(f"\n=== LHD-Full vs GDSF verdict at n=20 ===")
for a, r in results.items():
    p = r['welch_p_lhd_vs_gdsf']
    gap = r['lhd_minus_gdsf_pp']
    if p is None:
        verdict = "n/a"
    elif p < 0.05:
        winner = "LHD-Full" if gap < 0 else "GDSF"
        verdict = f"SIGNIFICANT, winner = {winner} (gap {gap:+.2f}pp, p={p:.4f})"
    else:
        verdict = f"not significant (gap {gap:+.2f}pp, p={p:.4f})"
    print(f"  alpha={a}: {verdict}")
