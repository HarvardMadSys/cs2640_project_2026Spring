#!/usr/bin/env python3
"""
Run Congress focal alpha cells at 20 seeds (vs the v2 paper's 5) to test
whether the "tied" findings in §6 are real ties or just underpowered.

The v2 paper claims SIEVE and W-TinyLFU are "statistically tied" on
Congress at α ∈ {0.6, 0.8, 1.0, 1.2}. With n=5, the test has weak power.
Re-run with n=20 and report Welch's t-test p-values.
"""
import csv
import shutil
import subprocess
import sys
import json
from pathlib import Path
from statistics import mean, stdev

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "cache_sim"
TRACE = ROOT / "traces" / "congress_trace.csv"
OUT_DIR = ROOT / "results" / "congress_high_seed"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SEEDS = list(range(42, 42+20))
ALPHAS = [0.6, 0.8, 1.0, 1.2]
CACHE_FRAC = 0.01

def run(alpha, seed):
    cell_dir = OUT_DIR / f"_run_a{alpha}_d{seed}"
    cell_dir.mkdir(parents=True, exist_ok=True)
    cmd = [str(SIM),
           "--trace", str(TRACE),
           "--replay-zipf",
           "--alpha", str(alpha),
           "--seed", str(seed),
           "--policies", "sieve,wtinylfu",
           "--cache-sizes", str(CACHE_FRAC),
           "--output-dir", str(cell_dir)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(f"FAIL {alpha} {seed}\n")
        return None
    out = {}
    with open(cell_dir / "mrc.csv") as f:
        for row in csv.DictReader(f):
            out[row["policy"]] = float(row["miss_ratio"])
    shutil.rmtree(cell_dir, ignore_errors=True)
    return out

def welch_t(a, b):
    """Welch's t-test for unequal variances. Returns (t, df, two-sided p_approx).
    p-value approximation uses the standard normal tail (good enough for df > 10)."""
    import math
    na, nb = len(a), len(b)
    if na < 2 or nb < 2: return None, None, None
    ma, mb = mean(a), mean(b)
    va, vb = stdev(a)**2, stdev(b)**2
    se = math.sqrt(va/na + vb/nb)
    if se == 0: return None, None, None
    t = (ma - mb) / se
    df_num = (va/na + vb/nb)**2
    df_den = (va/na)**2/(na-1) + (vb/nb)**2/(nb-1)
    df = df_num / df_den if df_den > 0 else None
    # two-sided p ≈ 2 * (1 - Phi(|t|))
    # use a series expansion or the erfc
    p = math.erfc(abs(t)/math.sqrt(2))
    return t, df, p

results = {}
print(f"Running Congress 20-seed alpha sweep at cache_frac={CACHE_FRAC}...")
for a in ALPHAS:
    sieve_mrs = []
    wtlfu_mrs = []
    for s in SEEDS:
        d = run(a, s)
        if d:
            sieve_mrs.append(d["SIEVE"])
            wtlfu_mrs.append(d["W-TinyLFU"])
    t, df, p = welch_t(sieve_mrs, wtlfu_mrs)
    gap = (mean(sieve_mrs) - mean(wtlfu_mrs)) * 100
    s_sieve = stdev(sieve_mrs) * 100
    s_wtlfu = stdev(wtlfu_mrs) * 100
    results[a] = {
        "n_seeds": len(sieve_mrs),
        "sieve_mean": round(mean(sieve_mrs), 5),
        "sieve_std":  round(stdev(sieve_mrs), 5),
        "wtlfu_mean": round(mean(wtlfu_mrs), 5),
        "wtlfu_std":  round(stdev(wtlfu_mrs), 5),
        "gap_pp":     round(gap, 3),
        "welch_t":    round(t, 3) if t else None,
        "welch_df":   round(df, 1) if df else None,
        "welch_p":    round(p, 4) if p else None,
    }
    print(f"  α={a}: SIEVE={mean(sieve_mrs):.4f}±{s_sieve:.4f}pp"
          f"  WTLFU={mean(wtlfu_mrs):.4f}±{s_wtlfu:.4f}pp"
          f"  gap={gap:+.2f}pp  t={t:.2f}  p={p:.3f}")

with open(OUT_DIR / "summary.json", "w") as f:
    json.dump(results, f, indent=2)
print(f"\nWrote {OUT_DIR / 'summary.json'}")

# Decisive verdict: is there a regime where p < 0.05 at n=20?
print(f"\n=== Power analysis verdict ===")
significant = [a for a, r in results.items() if r['welch_p'] and r['welch_p'] < 0.05]
if significant:
    print(f"Significant gaps detected at α ∈ {significant} — v2's 'tied' claim was underpowered")
else:
    print(f"No significant gap at any α (all p ≥ 0.05). The v2 'tied' claim survives 20-seed test.")
