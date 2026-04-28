"""
Sweep baselines + attack-idea variants over cache sizes, on the local
Twitter cluster52 trace (1M-row CSV in oracleGeneral schema).

Output: a pretty-printed table + a JSON dump suitable for pasting into
ATTACK_PLAN.md §8.

Usage:
  python3 run_experiments.py            # full sweep
  python3 run_experiments.py --quick    # 100k requests, fewer sizes (smoke)
  python3 run_experiments.py --algos S3FIFO,Stump,Promote
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Local imports — keep relative to plugins/
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from sim import process_trace, read_twitter_csv, TWITTER_CSV, TRACES
from baselines import FIFO, LRU, S3FIFO, SIEVE, ARC, TwoQ, LFU, Belady

# Optional native-speed dispatcher for canonical baselines.
try:
    from cachesim_runner import (
        is_cachesim_available, has_trace_file, run_cachesim_batch,
        CACHESIM_ALGOS,
    )
    _CACHESIM = is_cachesim_available()
except Exception:
    _CACHESIM = False
    CACHESIM_ALGOS = {}


def trace_iter(name: str, limit: int):
    """Materialize a named trace into a list for replay across algos."""
    if name not in TRACES:
        raise SystemExit(f"unknown trace '{name}'. options: {sorted(TRACES)}")
    return TRACES[name](limit)


def evaluate(name: str, ctor, cache_size: int, trace) -> tuple[float, float, float]:
    cache = ctor(cache_size)
    t0 = time.time()
    req_mr, byte_mr = process_trace(cache, trace)
    return req_mr, byte_mr, time.time() - t0


# Algorithm factory registry; attack-idea modules append to this on import.
ALGOS: dict[str, callable] = {
    "FIFO": lambda sz: FIFO(sz),
    "LRU": lambda sz: LRU(sz),
    "LFU": lambda sz: LFU(sz),
    "S3FIFO": lambda sz: S3FIFO(sz),
    "SIEVE": lambda sz: SIEVE(sz),
    "ARC": lambda sz: ARC(sz),
    "TwoQ": lambda sz: TwoQ(sz),
    "Belady": lambda sz: Belady(sz),
}
# Register cachesim-only algos (LeCaR, LIRS, Cacheus, ...) with a sentinel
# factory that signals "must dispatch via cachesim". This lets users include
# them in --algos lists without a Python implementation.
class _CachesimOnly:
    """Sentinel; raises if accidentally instantiated in Python."""
    def __init__(self, *a, **k):
        raise RuntimeError(
            "this algo only runs via cachesim; use a file-backed trace.")
for _name in CACHESIM_ALGOS:
    if _name not in ALGOS:
        ALGOS[_name] = (lambda sz, _n=_name: _CachesimOnly(_n))


def _try_register(modname: str, *, name_to_factory: dict[str, callable]) -> None:
    try:
        mod = __import__(modname)
    except Exception as e:
        print(f"  (skipping {modname}: {e})", file=sys.stderr)
        return
    if hasattr(mod, "ALGOS"):
        ALGOS.update(mod.ALGOS)
    elif hasattr(mod, "make"):
        ALGOS[name_to_factory["name"]] = mod.make


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--quick", action="store_true",
                   help="100k requests, two cache sizes")
    p.add_argument("--limit", type=int, default=None,
                   help="cap requests; default is the full local trace")
    p.add_argument("--algos", type=str, default=None,
                   help="comma-separated subset of algos")
    p.add_argument("--sizes", type=str, default=None,
                   help="comma-separated cache sizes")
    p.add_argument("--trace", type=str, default="twitter",
                   help=f"trace name; one of {sorted(TRACES)}")
    p.add_argument("--json", type=str, default=None,
                   help="path to write a JSON results dump")
    args = p.parse_args()

    # Register attack-idea modules if present.
    META_OPTIMA: dict[str, list[str]] = {}
    for m in ("admission_stump", "learned_promotion", "exp3_sizer", "s3fifo_static"):
        try:
            mod = __import__(m)
            if hasattr(mod, "ALGOS"):
                ALGOS.update(mod.ALGOS)
                print(f"  loaded {m}: {list(mod.ALGOS)}", file=sys.stderr)
            if hasattr(mod, "META_OPTIMA"):
                META_OPTIMA.update(mod.META_OPTIMA)
                print(f"  loaded {m} meta-optima: {list(mod.META_OPTIMA)}",
                      file=sys.stderr)
        except Exception as e:
            print(f"  (skipping {m}: {e})", file=sys.stderr)

    if args.quick:
        limit = 100_000
        sizes = [1_000, 10_000]
    else:
        limit = args.limit
        sizes = [1_000, 10_000, 100_000]

    if args.sizes:
        sizes = [int(s) for s in args.sizes.split(",")]
    algos = list(ALGOS.keys())
    if args.algos:
        algos = [a.strip() for a in args.algos.split(",")]

    print(f"loading trace='{args.trace}' (limit={limit}) ...", file=sys.stderr)
    t0 = time.time()
    trace = trace_iter(args.trace, limit)
    print(f"  {len(trace):,} requests, {time.time()-t0:.1f}s", file=sys.stderr)

    # Expand any meta-aliases into their underlying variants. Track which
    # variant names need to be run and which meta names need post-processing.
    needed_variants: list[str] = []
    seen: set[str] = set()
    meta_requests: list[str] = []  # preserves user's order
    for name in algos:
        if name in META_OPTIMA:
            meta_requests.append(name)
            for v in META_OPTIMA[name]:
                if v not in seen:
                    seen.add(v)
                    needed_variants.append(v)
        else:
            if name not in seen:
                seen.add(name)
                needed_variants.append(name)

    # Validate.
    for v in needed_variants:
        if v not in ALGOS:
            raise SystemExit(
                f"unknown algo '{v}'. options: {sorted(ALGOS) + list(META_OPTIMA)}")

    results = {}
    print(f"\n{'algo':<24}  {'cache':>10}  {'req_MR':>8}  {'byte_MR':>8}  {'sec':>6}")

    # ── Pre-batch cachesim-eligible algos per cache size ────────────────────
    # cachesim runs all eviction policies in parallel threads on a single
    # trace load, so batching is ~free. This also fixes our buggy Python ARC
    # by routing it to the canonical libCacheSim implementation.
    cs_results: dict[int, dict[str, tuple[float, float, float]]] = {}
    if _CACHESIM and has_trace_file(args.trace):
        cs_eligible = [n for n in needed_variants if n in CACHESIM_ALGOS]
        if cs_eligible:
            for sz in sizes:
                try:
                    print(f"  cachesim batch [cache={sz}]: {cs_eligible}",
                          file=sys.stderr)
                    cs_results[sz] = run_cachesim_batch(
                        args.trace, cs_eligible, sz,
                        ignore_size=True, num_req=limit)
                except Exception as e:
                    print(f"  cachesim batch failed at sz={sz}: {e}", file=sys.stderr)
                    cs_results[sz] = {}

    # Run each needed variant. Cachesim-eligible ones come from cs_results;
    # everything else (V4 family, NoPromote, etc.) runs in Python.
    for name in needed_variants:
        for sz in sizes:
            from_cachesim = (sz in cs_results and name in cs_results[sz])
            if from_cachesim:
                req_mr, byte_mr, secs = cs_results[sz][name]
            else:
                ctor = ALGOS[name]
                req_mr, byte_mr, secs = evaluate(name, ctor, sz, trace)
            results.setdefault(name, {})[sz] = {
                "req_mr": req_mr,
                "byte_mr": byte_mr,
                "secs": secs,
                "src": "cachesim" if from_cachesim else "python",
            }
            # Hide the cross-product variants if a meta-alias was requested.
            hide = (name not in algos) and any(
                name in META_OPTIMA[m] for m in meta_requests)
            if not hide:
                tag = "*" if from_cachesim else " "
                print(f"{name:<24}  {sz:>10}  {req_mr:>8.4f}  "
                      f"{byte_mr:>8.4f}  {secs:>6.1f}{tag}")

    # Compute and print meta-alias rows: minimum req_MR / byte_MR per cache size.
    for meta in meta_requests:
        variants = META_OPTIMA[meta]
        for sz in sizes:
            best_req = min(results[v][sz]["req_mr"] for v in variants)
            best_byte = min(results[v][sz]["byte_mr"] for v in variants)
            secs_total = sum(results[v][sz]["secs"] for v in variants)
            # Tag which variant won (by req_mr).
            winner = min(variants, key=lambda v: results[v][sz]["req_mr"])
            results.setdefault(meta, {})[sz] = {
                "req_mr": best_req,
                "byte_mr": best_byte,
                "secs": secs_total,
                "winner": winner,
            }
            tag = f"  [{winner}]"
            print(f"{meta:<24}  {sz:>10}  {best_req:>8.4f}  {best_byte:>8.4f}  "
                  f"{secs_total:>6.1f}{tag}")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=2)
        print(f"wrote {args.json}", file=sys.stderr)


if __name__ == "__main__":
    main()
