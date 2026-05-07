"""Where is the HNSW cost actually spent, shared vs private, and which part
contends under A+B?

For a given variant we run six probes. Each probe has two phases:
  warmup    run the probe's workers for --warmup s, discarding samples.
            This forces the OS page cache to load the HNSW index pages for
            each partition that the probe touches, so the measurement
            starts from a warm, steady state rather than cold-start I/O.
  measure   run the same workers for --measure s, recording latency.

Optional --drop-cache: before each probe, drop the OS page cache (sudo -n
sysctl vm.drop_caches=3), so every probe is apples-to-apples cold-start.
If unset, probes run back-to-back on the same warm cache.

Probes:
  solo:
    SOLO_SHARED_ONLY    one tenant, partition_names=[shared_partition]
    SOLO_PRIVATE_ONLY   one tenant, partition_names=[partition_A]
    SOLO_BOTH           one tenant, both partitions (the benchmark setup)

  concurrent (A and B doing equivalent work):
    CONC_SHARED_X_SHARED   both tenants on shared_partition only.
                           Both workers hit the SAME graph: maximum cache
                           reuse, pure CPU / index-lock / BW contention.
    CONC_PRIVATE_X_PRIVATE A on partition_A, B on partition_B. Disjoint
                           graphs of identical size; no cache reuse between
                           tenants. If per-worker QPS here is ~half of
                           SOLO_PRIVATE_ONLY, the interference is explained
                           by CPU alone; if it is less than half, the cache
                           capacity is tight.
    CONC_BOTH_X_BOTH       the regular benchmark scenario.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from queue import Queue, Empty

import numpy as np
import pyarrow.parquet as pq
from pymilvus import MilvusClient

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


def drop_caches() -> bool:
    try:
        subprocess.run(
            ["sudo", "-n", "/sbin/sysctl", "-q", "vm.drop_caches=3"],
            check=True, capture_output=True, timeout=10,
        )
        return True
    except Exception as e:
        print(f"  drop_caches failed: {e}")
        return False


def cached_kb() -> int:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("Cached:"):
                    return int(line.split()[1])
    except Exception:
        return -1
    return -1


def ensure_only_loaded(client, target: str) -> None:
    """Release every collection in the instance except `target`, so the cgroup's
    page cache is not split between the variant we are probing and other
    Loaded collections. Then load `target` if it is not already Loaded."""
    cols = client.list_collections()
    for col in cols:
        if col == target:
            continue
        try:
            st = client.get_load_state(col)
            if "Loaded" in str(st) and "NotLoad" not in str(st):
                print(f"  releasing other loaded collection: {col}")
                client.release_collection(col)
        except Exception as e:
            print(f"  release({col}) skipped: {e}")
    print(f"  loading target: {target}")
    client.load_collection(target)
    print(f"    state: {client.get_load_state(target)}")


def worker(client, collection, partitions, q_emb, ef, top_k,
           stop_evt, record_fn, start_pos=0):
    params = {"metric_type": "COSINE", "params": {"ef": ef}}
    q_list = q_emb.tolist()
    n = len(q_list)
    i = start_pos
    while not stop_evt.is_set():
        vec = q_list[i % n]
        t0 = time.perf_counter()
        client.search(
            collection_name=collection,
            data=[vec],
            anns_field="vector",
            search_params=params,
            limit=top_k,
            partition_names=partitions,
        )
        lat_ms = (time.perf_counter() - t0) * 1000.0
        record_fn(lat_ms)
        i += 1


def run_probe(client, collection, workers_cfg, warmup_s, measure_s, ef, top_k):
    """workers_cfg: list of (label, partitions, q_emb, rpt)."""
    stop_evt = threading.Event()
    # Shared recorder with phase gate.
    phase_box = ["warmup"]
    samples: dict[str, list[float]] = {}
    locks: dict[str, threading.Lock] = {}
    for label, *_ in workers_cfg:
        samples[label] = []
        locks[label] = threading.Lock()

    def make_recorder(label):
        bucket = samples[label]
        lock = locks[label]
        def rec(lat_ms):
            if phase_box[0] == "measure":
                with lock:
                    bucket.append(lat_ms)
        return rec

    threads = []
    for label, parts, emb, rpt in workers_cfg:
        rec = make_recorder(label)
        n = len(emb)
        for j in range(rpt):
            start_pos = (j * n) // max(rpt, 1)
            th = threading.Thread(
                target=worker,
                args=(client, collection, parts, emb, ef, top_k,
                      stop_evt, rec, start_pos),
                daemon=True,
                name=f"{label}-w{j}",
            )
            threads.append(th)
            th.start()

    # warmup window
    t_warmup_start = time.time()
    time.sleep(warmup_s)
    # flip to measurement
    phase_box[0] = "measure"
    t_measure_start = time.time()
    time.sleep(measure_s)
    stop_evt.set()
    for th in threads:
        th.join(timeout=10.0)
    t_end = time.time()

    out = {}
    measure_dur = max(t_end - t_measure_start, 1e-6)
    for label, parts, emb, rpt in workers_cfg:
        lats = samples[label]
        n = len(lats)
        if n == 0:
            out[label] = dict(n=0, qps=0.0, per_worker_qps=0.0,
                              p50=float("nan"), p95=float("nan"), p99=float("nan"),
                              rpt=rpt, partitions=parts)
            continue
        out[label] = dict(
            n=n,
            qps=n / measure_dur,
            per_worker_qps=n / measure_dur / rpt,
            p50=float(np.percentile(lats, 50)),
            p95=float(np.percentile(lats, 95)),
            p99=float(np.percentile(lats, 99)),
            rpt=rpt,
            partitions=parts,
        )
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True,
                    choices=["wiki_QC02_SS02", "wiki_QC02_SS08", "wiki_QC08_SS08"])
    ap.add_argument("--warmup", type=float, default=30.0)
    ap.add_argument("--measure", type=float, default=60.0)
    ap.add_argument("--rpt", type=int, default=8)
    ap.add_argument("--ef", type=int, default=200)
    ap.add_argument("--top-k", type=int, default=100)
    ap.add_argument("--uri", default="http://localhost:19530")
    ap.add_argument("--drop-cache", action="store_true",
                    help="drop OS page cache before each probe (cold-start parity)")
    args = ap.parse_args()

    collection = args.variant
    print(f"Variant: {collection}")
    print(f"  warmup={args.warmup}s measure={args.measure}s rpt={args.rpt} "
          f"ef={args.ef} k={args.top_k} drop_cache={args.drop_cache}")

    client = MilvusClient(uri=args.uri)
    ensure_only_loaded(client, collection)

    q = load_queries(args.variant)
    emb_a, emb_b = q["A"], q["B"]
    print(f"  queries: A={len(emb_a)}, B={len(emb_b)}")

    shared = ["shared_partition"]
    part_a = ["partition_A"]
    part_b = ["partition_B"]
    both_a = ["shared_partition", "partition_A"]
    both_b = ["shared_partition", "partition_B"]

    probes = [
        ("SOLO_SHARED_ONLY",
         [("A", shared, emb_a, args.rpt)]),
        ("SOLO_PRIVATE_ONLY",
         [("A", part_a, emb_a, args.rpt)]),
        ("SOLO_BOTH",
         [("A", both_a, emb_a, args.rpt)]),
        ("CONC_SHARED_X_SHARED",
         [("A", shared, emb_a, args.rpt),
          ("B", shared, emb_b, args.rpt)]),
        ("CONC_PRIVATE_X_PRIVATE",
         [("A", part_a, emb_a, args.rpt),
          ("B", part_b, emb_b, args.rpt)]),
        ("CONC_BOTH_X_BOTH",
         [("A", both_a, emb_a, args.rpt),
          ("B", both_b, emb_b, args.rpt)]),
    ]

    out_path = OUT / (
        f"per_partition_cost_{args.variant}"
        f"{'_coldstart' if args.drop_cache else '_warm'}.json"
    )
    tmp_path = out_path.with_suffix(".json.tmp")

    def flush(results, completed):
        payload = {
            "args": vars(args),
            "completed_probes": completed,
            "total_probes": len(probes),
            "results": results,
        }
        with tmp_path.open("w") as f:
            json.dump(payload, f, indent=2, default=str)
        os.replace(tmp_path, out_path)

    results = {}
    flush(results, 0)
    print(f"  output file: {out_path}")
    for i, (name, cfg) in enumerate(probes, start=1):
        if args.drop_cache:
            print(f"\n>>> dropping page cache before {name} ...")
            ok = drop_caches()
            print(f"    drop_caches ok={ok}  Cached={cached_kb():,} kB")
        print(f"\n>>> {name}  (warmup {args.warmup}s, measure {args.measure}s)")
        out = run_probe(client, collection, cfg, args.warmup, args.measure,
                        args.ef, args.top_k)
        for label, stats in out.items():
            print(f"   {label}: n={stats['n']:5d}  qps={stats['qps']:6.2f} "
                  f"per_worker={stats['per_worker_qps']:5.2f}  "
                  f"p50={stats['p50']:7.1f}ms p95={stats['p95']:7.1f}ms  "
                  f"parts={stats['partitions']}")
        results[name] = out
        flush(results, i)
        print(f"   -> updated {out_path.name} ({i}/{len(probes)})")

    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
