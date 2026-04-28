"""Comprehensive cross-trace sweep — drives run_experiments.py once per trace
and dumps per-trace JSON for ATTACK_PLAN.md §14.

Each trace gets two cache sizes. Cloudphysics-shape traces (small, block I/O)
use 500/5000; everything else uses 1000/10000.

Algos run per cell:
  cachesim batch (canonical, ~5s):
    FIFO, LRU, LFU, ARC, TwoQ, SIEVE, S3FIFO (T=2), Belady,
    LeCaR, LIRS, Cacheus, WTinyLFU, ClockPro, Hyperbolic, GDSF, LHD, QDLP, SLRU
  Python (~5-30s each):
    S3FIFO+LearnedV4 (default S=0.10),
    S3FIFO+LearnedV4+S{0.01,0.05,0.25}, S3FIFO+LearnedV4+OptS (meta),
    S3FIFO+NoPromote,
    S3FIFO+T{1,2,3}+S{0.01,0.05,0.10,0.25} (12 cells driving OptT/OptS/OptST).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
RUNNER = HERE / "run_experiments.py"
RESULT_DIR = HERE / "result"
RESULT_DIR.mkdir(exist_ok=True)


# (trace, [cache_sizes], request_limit). limit=None means "use the full trace".
TRACES: list[tuple[str, list[int], int | None]] = [
    # Twitter Twemcache KV
    ("twitter",      [1000, 10000], 500_000),
    ("cluster10",    [1000, 10000], 500_000),
    ("cluster26",    [1000, 10000], 500_000),
    ("cluster45",    [1000, 10000], 500_000),
    ("cluster50",    [1000, 10000], 500_000),
    # MSR Cambridge block I/O
    ("msr_hm_0",     [1000, 10000], 500_000),
    ("msr_proj_0",   [1000, 10000], 500_000),
    ("msr_prxy_0",   [1000, 10000], 500_000),
    # Alibaba block (small samples)
    ("alibaba_110",  [100, 500],    None),
    ("alibaba_185",  [100, 500],    None),
    # CloudPhysics (small)
    ("cloudphysics", [500, 5000],   None),
    ("w105",         [500, 5000],   500_000),
    # CDN
    ("wiki",         [1000, 10000], 500_000),
    ("meta_reag",    [1000, 10000], 500_000),
    # Meta storage block
    ("block1",       [1000, 10000], 500_000),
]


# Common algo set per cell. Order matters only for printing. The cachesim
# batcher chunks at 16 algos automatically.
CACHESIM_ALGOS = [
    "FIFO", "LRU", "LFU", "ARC", "TwoQ", "SIEVE", "S3FIFO", "Belady",
    "LeCaR", "LIRS", "Cacheus", "WTinyLFU", "ClockPro", "Hyperbolic",
    "GDSF", "LHD", "QDLP", "SLRU",
]
PYTHON_ALGOS = [
    "S3FIFO+LearnedV4",
    "S3FIFO+LearnedV4+OptS",  # meta over LearnedV4+S{0.01,0.05,0.10,0.25}
    "S3FIFO+NoPromote",
    "S3FIFO+OptT", "S3FIFO+OptS", "S3FIFO+OptST",  # meta over the 12 T×S cells
]

ALGO_LIST = ",".join(CACHESIM_ALGOS + PYTHON_ALGOS)


def run_one(trace: str, sizes: list[int], limit: int | None) -> dict:
    out_path = RESULT_DIR / f"sweep14_{trace}.json"
    cmd = [
        "python3.11", str(RUNNER),
        "--trace", trace,
        "--sizes", ",".join(str(s) for s in sizes),
        "--algos", ALGO_LIST,
        "--json", str(out_path),
    ]
    if limit is not None:
        cmd += ["--limit", str(limit)]

    print(f"\n{'='*70}\n>>> {trace}  sizes={sizes}  limit={limit}\n{'='*70}",
          flush=True)
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=False)
    dt = time.time() - t0
    print(f"<<< {trace}  done in {dt:.1f}s  rc={proc.returncode}",
          flush=True)
    if not out_path.exists():
        return {"trace": trace, "error": f"no JSON produced (rc={proc.returncode})"}
    with open(out_path) as f:
        return json.load(f)


def main() -> None:
    # Allow running a subset by command line.
    if len(sys.argv) > 1:
        names = set(sys.argv[1:])
        traces = [t for t in TRACES if t[0] in names]
    else:
        traces = TRACES

    summary: dict[str, dict] = {}
    for trace, sizes, limit in traces:
        try:
            summary[trace] = run_one(trace, sizes, limit)
        except Exception as e:
            print(f"!!! {trace} failed: {e}", flush=True)
            summary[trace] = {"error": str(e)}

    # Pretty-print a one-line summary per (trace, size) cell at the end.
    print(f"\n\n{'='*70}\nSWEEP SUMMARY\n{'='*70}")
    for trace, _, _ in traces:
        data = summary.get(trace, {})
        if "error" in data:
            print(f"{trace}: ERROR {data['error']}")
            continue
        for size, by_algo in (
            (sz, {a: r for a, r in data.items() if isinstance(r, dict) and sz in r})
            for sz in sorted({s for d in data.values() if isinstance(d, dict) for s in d})
        ):
            mr = {a: by_algo[a][size]["req_mr"] for a in by_algo
                  if "req_mr" in by_algo[a].get(size, {})}
            if not mr:
                continue
            best = min(mr, key=mr.get)
            print(f"  {trace:<12}  cache={size:<6}  best={best:<28} mr={mr[best]:.4f}")


if __name__ == "__main__":
    main()
