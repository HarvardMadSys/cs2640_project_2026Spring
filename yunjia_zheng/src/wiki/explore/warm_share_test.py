"""Does warming the page cache with tenant A reduce B's start-up tax?

Two phases, no drop_caches between them:

  PHASE 1  run A alone for `warm_sec` seconds (rpt workers, searching both
           of A's partitions). This populates the kernel page cache with
           shared_partition + partition_A index pages.

  PHASE 2  immediately run A and B concurrently for `measure_sec` seconds.
           B's partition_B has had NO traffic yet, so any cold-cache cost
           B pays must come from partition_B alone (partition_A and the
           shared_partition are already resident from phase 1). By
           comparing B's first-interval QPS against A's first-interval QPS
           we isolate the cold-cache penalty that comes strictly from the
           private partition.

Also records per-tenant QPS time series so you can see whether A "drops"
when B joins (steady-state contention on the shared partition and on the
CPU) versus B climbing from zero (its private index warming up).

Usage:
  python warm_share_test.py --variant wiki_QC08_SS08 --warm 60 --measure 60 --rpt 8
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import threading
import time
from pathlib import Path
from queue import Queue

import numpy as np
import pyarrow.parquet as pq
from pymilvus import MilvusClient

from per_partition_cost import ensure_only_loaded

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)


def load_queries(variant: str) -> dict[str, np.ndarray]:
    idir = ROOT / f"{variant}_info"
    tbl = pq.read_table(idir / "query_assignment.parquet")
    partitions = tbl.column("partition").to_pylist()
    q_idxs = tbl.column("q_idx").to_numpy()
    emb = np.load(idir / "query_embeddings.npy")
    out = {}
    for t in ("A", "B"):
        sel = np.array([p == t for p in partitions], dtype=bool)
        out[t] = emb[q_idxs[sel]]
    return out


PARTITIONS_FOR = {
    "A": ["shared_partition", "partition_A"],
    "B": ["shared_partition", "partition_B"],
}


def worker(client, collection, tenant, q_emb, ef, top_k,
           start_wall, t_end_fn, stop_evt, samples, start_pos=0):
    """Push (t_rel, lat_ms) samples; t_end_fn is called each iter to allow
    later extension."""
    parts = PARTITIONS_FOR[tenant]
    params = {"metric_type": "COSINE", "params": {"ef": ef}}
    n = len(q_emb)
    i = start_pos
    while time.time() < t_end_fn() and not stop_evt.is_set():
        vec = q_emb[i % n]
        t0 = time.perf_counter()
        client.search(
            collection_name=collection,
            data=[vec.tolist()],
            anns_field="vector",
            search_params=params,
            limit=top_k,
            partition_names=parts,
        )
        lat = (time.perf_counter() - t0) * 1000.0
        samples.append((time.time() - start_wall, lat))
        i += 1


def launch(client, collection, tenant, q_emb, ef, top_k, rpt,
           start_wall, t_end_fn, stop_evt):
    threads = []
    samples: list[tuple[float, float]] = []
    # each worker appends to its own list to avoid GIL contention on shared list
    per_worker_samples: list[list] = [[] for _ in range(rpt)]
    n = len(q_emb)
    for j in range(rpt):
        start_pos = (j * n) // max(rpt, 1)
        th = threading.Thread(
            target=worker,
            args=(client, collection, tenant, q_emb, ef, top_k,
                  start_wall, t_end_fn, stop_evt, per_worker_samples[j],
                  start_pos),
            daemon=True, name=f"{tenant}-w{j}",
        )
        threads.append(th)
        th.start()
    return threads, per_worker_samples


def windowed_qps(samples: list[tuple[float, float]], window: float, t_lo: float,
                 t_hi: float) -> list[tuple[float, float]]:
    """Bucket sample times into `window`-sized bins over [t_lo, t_hi]."""
    if not samples:
        return []
    import math
    times = [s[0] for s in samples]
    out = []
    t = t_lo
    while t < t_hi:
        cnt = sum(1 for x in times if t <= x < t + window)
        out.append((t + window / 2, cnt / window))
        t += window
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True,
                    choices=["wiki_QC02_SS02", "wiki_QC02_SS08", "wiki_QC08_SS08"])
    ap.add_argument("--warm", type=float, default=60.0,
                    help="seconds to run A alone first")
    ap.add_argument("--measure", type=float, default=60.0,
                    help="seconds to run A+B concurrently after warm")
    ap.add_argument("--rpt", type=int, default=8)
    ap.add_argument("--ef", type=int, default=200)
    ap.add_argument("--top-k", type=int, default=100)
    ap.add_argument("--window", type=float, default=5.0,
                    help="QPS bucket window in seconds")
    ap.add_argument("--uri", default="http://localhost:19530")
    args = ap.parse_args()

    collection = args.variant
    print(f"Variant {collection}  warm={args.warm}s  measure={args.measure}s  "
          f"rpt={args.rpt}")

    client = MilvusClient(uri=args.uri)
    ensure_only_loaded(client, collection)
    q = load_queries(args.variant)
    emb_a, emb_b = q["A"], q["B"]

    # Light warmup to bring QueryNode segment state up; doesn't try to warm
    # the cache.  Just a single search per tenant.
    params = {"metric_type": "COSINE", "params": {"ef": args.ef}}
    for t in ("A", "B"):
        client.search(
            collection_name=collection, data=[q[t][0].tolist()],
            anns_field="vector", search_params=params, limit=args.top_k,
            partition_names=PARTITIONS_FOR[t],
        )

    # File paths up front so we can flush incrementally and the user can tail.
    csv_path = OUT / f"warm_share_{args.variant}.csv"
    json_path = OUT / f"warm_share_{args.variant}.json"

    def atomic_write(path, write_fn):
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", newline="") as f:
            write_fn(f)
        os.replace(tmp, path)

    def band(samples, t_lo, t_hi):
        s = [x for x in samples if t_lo <= x[0] < t_hi]
        n = len(s)
        if n == 0:
            return dict(n=0, qps=0.0, p50=float("nan"), p95=float("nan"))
        lats = [x[1] for x in s]
        return dict(n=n, qps=n / max(t_hi - t_lo, 1e-6),
                    p50=float(np.percentile(lats, 50)),
                    p95=float(np.percentile(lats, 95)))

    def flush(a_samples, b_samples, status, wall_end):
        a_flat = [s for ws in a_samples for s in ws]
        b_flat = [s for ws in b_samples for s in ws]
        # Time-bucketed CSV.
        qps_a = windowed_qps(a_flat, args.window, 0, max(wall_end, 1e-3))
        qps_b = windowed_qps(b_flat, args.window, 0, max(wall_end, 1e-3))
        rows_by_t = {t: [0.0, 0.0] for t, _ in qps_a}
        for t, q in qps_a:
            rows_by_t.setdefault(t, [0.0, 0.0])[0] = q
        for t, q in qps_b:
            rows_by_t.setdefault(t, [0.0, 0.0])[1] = q

        def _write_csv(f):
            w = csv.writer(f)
            w.writerow(["t_rel_s", "qps_A", "qps_B", "phase"])
            for t in sorted(rows_by_t):
                ph = "warm" if t < args.warm else "measure"
                w.writerow([f"{t:.2f}", f"{rows_by_t[t][0]:.2f}",
                            f"{rows_by_t[t][1]:.2f}", ph])
        atomic_write(csv_path, _write_csv)

        summary = {
            "variant": args.variant,
            "rpt": args.rpt,
            "status": status,
            "wall_end_s": wall_end,
            "n_a_samples": len(a_flat),
            "n_b_samples": len(b_flat),
            "A_warm":               band(a_flat, 0, args.warm),
            "A_warm_last20s":       band(a_flat, max(args.warm - 20, 0), args.warm),
            "A_concurrent":         band(a_flat, args.warm, max(wall_end, args.warm)),
            "A_concurrent_first10": band(a_flat, args.warm, args.warm + 10),
            "A_concurrent_last20s": band(a_flat,
                                         max(wall_end - 20, args.warm),
                                         max(wall_end, args.warm)),
            "B_first10":            band(b_flat, args.warm, args.warm + 10),
            "B_last20s":            band(b_flat,
                                         max(wall_end - 20, args.warm),
                                         max(wall_end, args.warm)),
        }
        atomic_write(json_path,
                     lambda f: json.dump(summary, f, indent=2, default=str))
        return summary

    # Touch the output files immediately.
    flush([], [], status="starting", wall_end=0.0)
    print(f"  outputs: {json_path}, {csv_path}")

    # Phase 1: A alone.
    print("\n=== PHASE 1: A alone (warm) ===")
    stop_evt = threading.Event()
    start_wall = time.time()
    phase1_end = start_wall + args.warm
    final_end = phase1_end + args.measure
    # t_end_fn lives in a box so we can extend A's lifetime into phase 2.
    t_end_box = [final_end]

    a_threads, a_samples = launch(
        client, collection, "A", emb_a, args.ef, args.top_k, args.rpt,
        start_wall, lambda: t_end_box[0], stop_evt,
    )

    # Wait for phase 1 end, letting A accrue samples; flush every 10 s.
    next_flush = time.time() + 10.0
    while time.time() < phase1_end:
        time.sleep(min(0.5, max(phase1_end - time.time(), 0.01)))
        if time.time() >= next_flush:
            wall = time.time() - start_wall
            flush(a_samples, [[]], status="in_progress_warm", wall_end=wall)
            print(f"   [flush warm] t={wall:.1f}s "
                  f"a_samples={sum(len(x) for x in a_samples)}")
            next_flush = time.time() + 10.0
    flush(a_samples, [[]], status="warm_done",
          wall_end=time.time() - start_wall)
    print(f"  phase 1 done at t={time.time() - start_wall:.1f}s")

    # Phase 2: add B.
    print("\n=== PHASE 2: A + B ===")
    b_threads, b_samples = launch(
        client, collection, "B", emb_b, args.ef, args.top_k, args.rpt,
        start_wall, lambda: t_end_box[0], stop_evt,
    )

    next_flush = time.time() + 10.0
    while time.time() < final_end:
        time.sleep(min(0.5, max(final_end - time.time(), 0.01)))
        if time.time() >= next_flush:
            wall = time.time() - start_wall
            flush(a_samples, b_samples,
                  status="in_progress_measure", wall_end=wall)
            print(f"   [flush measure] t={wall:.1f}s "
                  f"a={sum(len(x) for x in a_samples)} "
                  f"b={sum(len(x) for x in b_samples)}")
            next_flush = time.time() + 10.0
    stop_evt.set()
    for th in a_threads + b_threads:
        th.join(timeout=5.0)
    wall_end = time.time() - start_wall
    print(f"  phase 2 done at t={wall_end:.1f}s")

    summary = flush(a_samples, b_samples, status="done", wall_end=wall_end)
    print(f"  A samples: {summary['n_a_samples']}  "
          f"B samples: {summary['n_b_samples']}")
    print(f"Wrote {csv_path}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
