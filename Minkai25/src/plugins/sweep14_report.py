"""Analyze sweep14 JSON dumps and generate the §14 markdown table for ATTACK_PLAN.md.

Reads plugins/result/sweep14_*.json (one file per trace, written by sweep14.py)
and produces:
  - a per-cell headline table comparing canonical baselines, V4, V4+OptS, OptST
  - a win/loss tally
  - a "best classical baseline" column
  - per-trace narrative observations
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
RESULT_DIR = HERE / "result"


# Canonical-classical baselines we want to highlight in the headline columns.
# Order matters: "best non-V4 non-Belady" picks min over this set.
CLASSICAL = [
    "FIFO", "LRU", "LFU", "ARC", "TwoQ", "SIEVE", "LeCaR", "LIRS", "Cacheus",
    "WTinyLFU", "ClockPro", "Hyperbolic", "GDSF", "LHD", "QDLP", "SLRU",
    "S3FIFO",
]


def best_of(d: dict[str, float], names: list[str]) -> tuple[str, float] | None:
    avail = {n: d[n] for n in names if n in d}
    if not avail:
        return None
    k = min(avail, key=avail.get)
    return k, avail[k]


def load_all() -> dict[str, dict]:
    out = {}
    for path in sorted(RESULT_DIR.glob("sweep14_*.json")):
        trace = path.stem.replace("sweep14_", "")
        try:
            with open(path) as f:
                out[trace] = json.load(f)
        except Exception as e:
            print(f"!! {path}: {e}", file=sys.stderr)
    return out


def cells(data: dict) -> list[tuple[str, int]]:
    """Return the (algo, size) cells present in a loaded JSON."""
    sizes = set()
    for algo, by_sz in data.items():
        if not isinstance(by_sz, dict):
            continue
        for sz_str in by_sz:
            try:
                sizes.add(int(sz_str))
            except Exception:
                pass
    return sorted(sizes)


def algo_mr(data: dict, algo: str, size: int) -> float | None:
    by_sz = data.get(algo)
    if not isinstance(by_sz, dict):
        return None
    cell = by_sz.get(str(size)) or by_sz.get(size)
    if not isinstance(cell, dict):
        return None
    return cell.get("req_mr")


def main() -> None:
    all_data = load_all()
    if not all_data:
        print("No sweep14_*.json found.", file=sys.stderr)
        return

    # OptS_T2: best of S3FIFO+T2+S{0.01,0.05,0.10,0.25} per cell — the canonical
    # S=auto sweep with T fixed at the paper's default. Computed from JSON;
    # underlying cells are run via the OptST meta in sweep14.
    T2_S_VARIANTS = ["S3FIFO+T2+S0.01", "S3FIFO+T2+S0.05",
                     "S3FIFO+T2+S0.10", "S3FIFO+T2+S0.25"]

    def opt_s_t2(data: dict, sz: int) -> float | None:
        mrs = [algo_mr(data, v, sz) for v in T2_S_VARIANTS]
        mrs = [m for m in mrs if m is not None]
        return min(mrs) if mrs else None

    rows = []
    for trace, data in all_data.items():
        for sz in cells(data):
            row = {
                "trace": trace,
                "size": sz,
                "Belady":   algo_mr(data, "Belady", sz),
                "S3FIFO":   algo_mr(data, "S3FIFO", sz),       # canonical T=2
                "OptT":     algo_mr(data, "S3FIFO+OptT", sz),
                "OptS":     algo_mr(data, "S3FIFO+OptS", sz),       # T=1 (non-canonical)
                "OptS_T2":  opt_s_t2(data, sz),                      # T=2 canonical
                "OptST":    algo_mr(data, "S3FIFO+OptST", sz),
                "V4":       algo_mr(data, "S3FIFO+LearnedV4", sz),
                "V4+OptS":  algo_mr(data, "S3FIFO+LearnedV4+OptS", sz),
                "NoPromote": algo_mr(data, "S3FIFO+NoPromote", sz),
            }
            mrs_class = {n: algo_mr(data, n, sz) for n in CLASSICAL}
            mrs_class = {k: v for k, v in mrs_class.items() if v is not None}
            if mrs_class:
                bn = min(mrs_class, key=mrs_class.get)
                row["best_classical"] = (bn, mrs_class[bn])
            rows.append(row)

    # ── Headline table ────────────────────────────────────────────────────
    print("\n## §14 headline table (req-MR)\n")
    print("| trace | cache | Belady | best classical (alg) | S3FIFO (T=2) | "
          "OptS_T2 | OptST | V4 | V4+OptS | NoPromote |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        bc = r.get("best_classical")
        bc_str = f"{bc[1]:.4f} ({bc[0]})" if bc else "—"
        def f(v): return f"{v:.4f}" if v is not None else "—"
        print(f"| {r['trace']} | {r['size']:,} | {f(r['Belady'])} | {bc_str} | "
              f"{f(r['S3FIFO'])} | {f(r['OptS_T2'])} | {f(r['OptST'])} | "
              f"{f(r['V4'])} | {f(r['V4+OptS'])} | {f(r['NoPromote'])} |")

    # ── Win/loss tally for V4 and V4+OptS vs canonical S3FIFO and OptST ──
    print("\n## §14 win/loss tally\n")
    tallies = {"V4 vs S3FIFO(T=2)": [0, 0, 0],
               "V4+OptS vs S3FIFO(T=2)": [0, 0, 0],
               "V4+OptS vs OptS_T2": [0, 0, 0],
               "V4 vs OptST": [0, 0, 0],
               "V4+OptS vs OptST": [0, 0, 0],
               "V4+OptS vs best classical": [0, 0, 0],
               "OptS_T2 vs S3FIFO(T=2)": [0, 0, 0],
               "NoPromote vs S3FIFO(T=2)": [0, 0, 0]}
    deltas = {k: [] for k in tallies}
    EPS = 0.001  # 0.1 pp
    def cmp(a, b, key):
        if a is None or b is None:
            return
        d = b - a  # positive = a better
        deltas[key].append((d, ))
        if d > EPS: tallies[key][0] += 1   # win
        elif d < -EPS: tallies[key][1] += 1  # loss
        else: tallies[key][2] += 1           # tie
    for r in rows:
        if r["trace"] in {"zipf_heavy", "zipf_flat", "cluster10"}:
            continue  # control / no signal
        cmp(r["V4"], r["S3FIFO"], "V4 vs S3FIFO(T=2)")
        cmp(r["V4+OptS"], r["S3FIFO"], "V4+OptS vs S3FIFO(T=2)")
        cmp(r["V4+OptS"], r["OptS_T2"], "V4+OptS vs OptS_T2")
        cmp(r["V4"], r["OptST"], "V4 vs OptST")
        cmp(r["V4+OptS"], r["OptST"], "V4+OptS vs OptST")
        bc = r.get("best_classical")
        if bc is not None:
            cmp(r["V4+OptS"], bc[1], "V4+OptS vs best classical")
        cmp(r["OptS_T2"], r["S3FIFO"], "OptS_T2 vs S3FIFO(T=2)")
        cmp(r["NoPromote"], r["S3FIFO"], "NoPromote vs S3FIFO(T=2)")
    print("| comparison | wins | losses | ties | mean Δ (pp) |")
    print("|---|---|---|---|---|")
    for k, (w, l, t) in tallies.items():
        if not deltas[k]:
            print(f"| {k} | — | — | — | — |")
        else:
            mean_d = sum(d[0] for d in deltas[k]) / len(deltas[k]) * 100
            print(f"| {k} | {w} | {l} | {t} | {mean_d:+.2f} |")


if __name__ == "__main__":
    main()
