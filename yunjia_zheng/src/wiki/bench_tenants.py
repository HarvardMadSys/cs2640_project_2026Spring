"""Per-tenant Milvus search benchmark.

Each tenant has its own set of queries (assigned in prepare_wiki_partitions.py)
and searches only the partitions it owns: shared_partition + partition_A for
tenant A, shared_partition + partition_B for tenant B.

Modes:
  python bench_tenants.py                   # two threads, A and B concurrently
  python bench_tenants.py --tenant A        # single thread, A only
  python bench_tenants.py --tenant B        # single thread, B only

Each tenant thread loops its query list in order for --duration seconds.
Per-query latency is recorded; rolling throughput per --interval seconds is
printed to stdout, and a per-query CSV is written under wiki/info/bench/.
"""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import threading
import time
from pathlib import Path
from queue import Queue, Empty

import numpy as np
import pyarrow.parquet as pq


HERE = Path(__file__).resolve().parent
BENCH_DIR = HERE / "bench_result"


def info_dir_for(collection: str) -> Path:
    """Per-collection info dir lookup.

    Tries, in order:
      <HERE>/<collection>_info/
      <HERE>/<collection minus last _segment>_info/
      <HERE>/<collection minus last two _segments>_info/
      ...
      <HERE>/info/                     # legacy fallback

    This lets variants of the same partition layout share an info dir, e.g.
    `wiki_QC02_SS02_nocompact` and `wiki_QC02_SS02` both resolve to
    `<HERE>/wiki_QC02_SS02_info/`. Tweaks like `_nocompact`, `_smallindex`,
    or any other Milvus-side variant suffix are stripped automatically as
    long as the underlying query/partition assignment is the same.
    """
    parts = collection.split("_")
    while parts:
        cand = HERE / f"{'_'.join(parts)}_info"
        if cand.is_dir():
            return cand
        parts.pop()
    return HERE / "info"

DEFAULT_DATA_DIR = "/scratch/yunjia/milvus-data"

PARTITIONS_FOR = {
    "A": ["shared_partition", "partition_A"],
    "B": ["shared_partition", "partition_B"],
}


def ensure_only_loaded(client, target: str, log_fn=print) -> None:
    """Release every collection in the instance except `target`, so the cgroup's
    page cache is dedicated to the variant we are benchmarking. Then load
    `target` if it is not already Loaded."""
    cols = client.list_collections()
    for col in cols:
        if col == target:
            continue
        try:
            st = client.get_load_state(col)
            if "Loaded" in str(st) and "NotLoad" not in str(st):
                log_fn(f"  releasing other loaded collection: {col}")
                client.release_collection(col)
        except Exception as e:
            log_fn(f"  release({col}) skipped: {e}")
    log_fn(f"  loading target: {target}")
    client.load_collection(target)
    log_fn(f"    state: {client.get_load_state(target)}")


def load_tenant_queries(info_dir: Path) -> tuple[dict[str, tuple[np.ndarray, np.ndarray]], np.ndarray]:
    """Return ({'A': (q_idxs, emb), 'B': (q_idxs, emb)}, gt_knn[Q x k]).

    Reads query_assignment.parquet, query_embeddings.npy, and knn_indices.npy
    from `info_dir`. Each collection variant should have its own info dir so
    benchmarks against `wiki_QC02_SS02` use that variant's partition labels
    rather than the labels of whatever was last written to `wiki/info/`.
    """
    assign_path = info_dir / "query_assignment.parquet"
    qemb_path   = info_dir / "query_embeddings.npy"
    knn_path    = info_dir / "knn_indices.npy"
    for p in (assign_path, qemb_path, knn_path):
        if not p.exists():
            raise FileNotFoundError(
                f"{p} not found. Expected per-collection info dir "
                f"'{info_dir}'. Either rename your existing 'info/' to "
                f"'<collection>_info/' or pass --info-dir explicitly."
            )
    tbl = pq.read_table(assign_path)
    partitions = tbl.column("partition").to_pylist()
    q_idxs_all = tbl.column("q_idx").to_numpy()
    emb_all = np.load(qemb_path)
    gt = np.load(knn_path)

    out = {}
    for t in ("A", "B"):
        sel = np.array([p == t for p in partitions], dtype=bool)
        q_idxs = q_idxs_all[sel]
        emb = emb_all[q_idxs]
        out[t] = (q_idxs, emb)
    return out, gt


def reset_page_cache(data_dir: str, log_fn) -> tuple[int, int]:
    """Drop OS page cache for files under `data_dir`.

    First tries `sudo -n /sbin/sysctl vm.drop_caches=3` (most thorough, needs
    passwordless sudo). Falls back to `posix_fadvise(DONTNEED)` per file,
    which only drops pages that aren't currently mapped by any process. For
    the fadvise path to actually evict, call this AFTER releasing the
    collection so Milvus's mappings are gone.

    Returns (files_touched, bytes_cached_after) where bytes_cached_after is
    an informational snapshot of system-wide Cached kB from /proc/meminfo.
    """
    # Try drop_caches first
    try:
        subprocess.run(
            ["sudo", "-n", "/sbin/sysctl", "-q", "vm.drop_caches=3"],
            check=True, capture_output=True, timeout=10,
        )
        log_fn("  drop_caches=3 applied via sudo")
        files = -1  # sentinel: system-wide
    except Exception:
        # Fall back to per-file fadvise
        files = 0
        for root, _, names in os.walk(data_dir):
            for name in names:
                p = os.path.join(root, name)
                try:
                    fd = os.open(p, os.O_RDONLY)
                    try:
                        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
                        files += 1
                    finally:
                        os.close(fd)
                except Exception:
                    pass
        log_fn(f"  posix_fadvise(DONTNEED) on {files} files under {data_dir}")

    cached = -1
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("Cached:"):
                    cached = int(line.split()[1])  # kB
                    break
    except Exception:
        pass
    return files, cached


def tenant_worker(
    client,
    tenant: str,
    collection: str,
    q_idxs: np.ndarray,
    q_emb: np.ndarray,
    duration: float,
    top_k: int,
    ef: int,
    stop_evt: threading.Event,
    recall_q: Queue,
    counter: list[int],
    start_wall: float,
    start_pos: int = 0,
):
    """Issue searches in a tight loop; offload recall work to a consumer thread.

    Each worker waits for its own previous search to return before submitting
    the next. Workers run on independent threads and do not coordinate with
    each other, so when multiple workers are active (multiple tenants, or
    --request-per-tenant > 1), all of them are making gRPC calls into Milvus
    concurrently (pymilvus's MilvusClient is thread-safe).
    """
    partitions = PARTITIONS_FOR[tenant]
    search_params = {"metric_type": "COSINE", "params": {"ef": ef}}
    q_emb_list = q_emb.tolist()
    n = len(q_emb_list)
    t_end = start_wall + duration
    i = start_pos
    while time.time() < t_end and not stop_evt.is_set():
        pos = i % n
        vec = q_emb_list[pos]
        qid = int(q_idxs[pos])
        t0 = time.perf_counter()
        res = client.search(
            collection_name=collection,
            data=[vec],
            anns_field="vector",
            search_params=search_params,
            limit=top_k,
            partition_names=partitions,
        )
        lat_ms = (time.perf_counter() - t0) * 1000.0
        rel = time.time() - start_wall
        # Minimal work on the hot path: grab IDs and enqueue.
        returned_ids = [h["id"] for h in res[0]]
        recall_q.put((tenant, pos, qid, lat_ms, rel, returned_ids))
        counter[0] += 1
        i += 1


def recall_consumer(
    recall_q: Queue,
    gt_sets_per_tenant: dict[str, list[set[int]]],
    records: dict[str, list[tuple[float, int, float, int]]],
    hits_counters: dict[str, list[int]],
    stop_evt: threading.Event,
):
    """Drain recall_q, compute hits, append to records and hits_counters.
    Keeps running until stop_evt is set AND the queue has been drained."""
    while True:
        try:
            item = recall_q.get(timeout=0.1)
        except Empty:
            if stop_evt.is_set():
                return
            continue
        tenant, pos, qid, lat_ms, rel, returned_ids = item
        hits = 0
        gt = gt_sets_per_tenant[tenant][pos]
        for v in returned_ids:
            if int(v) in gt:
                hits += 1
        records[tenant].append((rel, qid, lat_ms, hits))
        hits_counters[tenant][0] += hits


def interval_monitor(
    counters: dict[str, list[int]],
    hits_counters: dict[str, list[int]],
    top_k: int,
    stop_evt: threading.Event,
    interval: float,
    start_wall: float,
    interval_rows: list[dict],
    log_fn,
):
    prev_n = {t: 0 for t in counters}
    prev_t = start_wall
    while not stop_evt.wait(interval):
        now = time.time()
        dt = now - prev_t
        prev_t = now
        row = {"t_rel_s": now - start_wall, "dt_s": dt}
        parts = []
        for t in counters:
            cur_n = counters[t][0]
            cur_h = hits_counters[t][0]
            dn = cur_n - prev_n[t]
            prev_n[t] = cur_n
            qps = dn / max(dt, 1e-9)
            # Cumulative recall from run start, not per-interval.
            # Small samples per interval are noisy, cumulative is what we care about.
            cum_recall = (cur_h / (cur_n * top_k)) if cur_n > 0 else float("nan")
            row[f"qps_{t}"] = qps
            row[f"cum_recall_{t}"] = cum_recall
            row[f"completed_{t}"] = dn
            row[f"total_{t}"] = cur_n
            parts.append(
                f"{t} qps={qps:7.1f}  cum_recall@{top_k}={cum_recall:.4f}  "
                f"total={cur_n}"
            )
        interval_rows.append(row)
        log_fn(f"[t+{row['t_rel_s']:6.1f}s]  " + "  ".join(parts))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", choices=["A", "B"], default=None,
                    help="If set, run only that tenant on one thread. "
                         "If omitted, run A and B concurrently on two threads.")
    ap.add_argument("--duration", type=float, default=30.0)
    ap.add_argument("--top-k", type=int, default=100)
    ap.add_argument("--ef", type=int, default=200)
    ap.add_argument("--interval", type=float, default=5.0,
                    help="Throughput-logging interval in seconds.")
    ap.add_argument("--uri", default="http://localhost:19530")
    ap.add_argument("--collection", default="wiki")
    ap.add_argument("--out-tag", default=None,
                    help="Optional tag appended to CSV filenames.")
    ap.add_argument("--request-per-tenant", type=int, default=8,
                    help="Number of concurrent in-flight searches per tenant "
                         "(default 1). Spawns that many independent worker "
                         "threads, each looping through the tenant's query "
                         "list with its own starting offset.")
    ap.add_argument("--reset-cache", action="store_true",
                    help="Before measuring, release the collection, drop the "
                         "OS page cache for Milvus's local storage, then "
                         "reload. Ensures each run starts with a cold cache.")
    ap.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                    help="Milvus local storage root for --reset-cache.")
    ap.add_argument("--warm", type=float, default=20.0,
                    help="Seconds to pre-run the same workload before the "
                         "measurement window. Samples from the warm phase "
                         "are discarded; the goal is to bring HNSW pages "
                         "into the OS page cache. Set to 0 to skip. If "
                         "--reset-cache is also set, the order is "
                         "reset -> warm -> measure.")
    ap.add_argument("--info-dir", default=None,
                    help="Directory holding query_assignment.parquet, "
                         "query_embeddings.npy, knn_indices.npy for the "
                         "collection being benchmarked. Defaults to "
                         "<bench_tenants.py dir>/<collection>_info/, with "
                         "fallback to legacy <bench_tenants.py dir>/info/.")
    args = ap.parse_args()

    from pymilvus import MilvusClient

    tenants = [args.tenant] if args.tenant else ["A", "B"]
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    tag = args.out_tag or (
        "_".join(tenants) + f"_dur{int(args.duration)}s_ef{args.ef}_k{args.top_k}"
    )
    log_path = BENCH_DIR / f"run_{tag}.log"
    log_fh = log_path.open("w")

    def log(msg: str) -> None:
        print(msg, flush=True)
        log_fh.write(msg + "\n")
        log_fh.flush()

    log(f"Args: {vars(args)}")
    info_dir = Path(args.info_dir) if args.info_dir else info_dir_for(args.collection)
    log(f"info_dir={info_dir}")
    log(f"Connecting to {args.uri} ...")
    client = MilvusClient(uri=args.uri)
    ensure_only_loaded(client, args.collection, log_fn=log)

    log(f"Loading query assignments, embeddings, and ground-truth KNN from {info_dir} ...")
    tq, gt_knn = load_tenant_queries(info_dir)
    log(f"  gt_knn shape={gt_knn.shape}")
    for t in tenants:
        log(f"  {t}: {len(tq[t][0])} queries, dim={tq[t][1].shape[1]}")

    # Optional cold-cache reset: release collection, drop page cache, reload.
    if args.reset_cache:
        log("Resetting page cache (release -> drop -> reload) ...")
        try:
            client.release_collection(args.collection)
            log("  released collection")
        except Exception as e:
            log(f"  release failed (ok if already released): {e}")
        # Small pause so the kernel finalizes any pending munmaps.
        time.sleep(1.0)
        nfiles, cached_kb = reset_page_cache(args.data_dir, log)
        if cached_kb > 0:
            log(f"  system Cached after reset: {cached_kb:,} kB")
        log("  reloading collection ...")
        client.load_collection(args.collection)
        log(f"  load state: {client.get_load_state(args.collection)}")

    # Warm up the QueryNode so the first iteration isn't a cold hit.
    log("Warming up ...")
    for t in tenants:
        client.search(
            collection_name=args.collection,
            data=[tq[t][1][0].tolist()],
            anns_field="vector",
            search_params={"metric_type": "COSINE", "params": {"ef": args.ef}},
            limit=args.top_k,
            partition_names=PARTITIONS_FOR[t],
        )

    # Optional cache-warming phase: replay the same workload for --warm seconds
    # with samples discarded, so the measurement starts on warm HNSW pages.
    if args.warm > 0:
        rpt_warm = max(1, int(args.request_per_tenant))
        log(f"Warming page cache by replaying queries for {args.warm:.0f}s "
            f"(rpt={rpt_warm} per tenant) ...")
        warm_stop = threading.Event()
        warm_t_end = time.time() + args.warm
        warm_params = {"metric_type": "COSINE", "params": {"ef": args.ef}}

        def _warm_worker(tenant: str, q_emb: np.ndarray, start_pos: int):
            partitions = PARTITIONS_FOR[tenant]
            n = len(q_emb)
            i = start_pos
            while time.time() < warm_t_end and not warm_stop.is_set():
                client.search(
                    collection_name=args.collection,
                    data=[q_emb[i % n].tolist()],
                    anns_field="vector",
                    search_params=warm_params,
                    limit=args.top_k,
                    partition_names=partitions,
                )
                i += 1

        warm_threads = []
        for t in tenants:
            _, q_emb = tq[t]
            n = len(q_emb)
            for j in range(rpt_warm):
                start_pos = (j * n) // rpt_warm if rpt_warm > 1 else 0
                th = threading.Thread(
                    target=_warm_worker, args=(t, q_emb, start_pos),
                    name=f"warm-{t}-w{j}", daemon=True,
                )
                warm_threads.append(th)
                th.start()
        for th in warm_threads:
            th.join(timeout=args.warm + 30.0)
        log(f"  warm done after {args.warm:.0f}s")

    records: dict[str, list[tuple[float, int, float, int]]] = {t: [] for t in tenants}
    counters = {t: [0] for t in tenants}
    hits_counters = {t: [0] for t in tenants}

    # Pre-build per-tenant ground-truth sets indexed by position in the tenant's list.
    gt_sets_per_tenant: dict[str, list[set[int]]] = {}
    for t in tenants:
        q_idxs = tq[t][0]
        gt_sets_per_tenant[t] = [
            set(int(x) for x in gt_knn[int(q_idxs[j])][: args.top_k])
            for j in range(len(q_idxs))
        ]

    recall_q: Queue = Queue()
    workers_stop = threading.Event()      # set when tenant workers finish
    consumer_stop = threading.Event()     # set after queue has been drained
    monitor_stop = threading.Event()      # set at the very end

    start_wall = time.time()
    interval_rows: list[dict] = []
    mon = threading.Thread(
        target=interval_monitor,
        args=(counters, hits_counters, args.top_k, monitor_stop,
              args.interval, start_wall, interval_rows, log),
        daemon=True,
    )
    mon.start()

    consumer = threading.Thread(
        target=recall_consumer,
        args=(recall_q, gt_sets_per_tenant, records, hits_counters, consumer_stop),
        name="recall-consumer",
        daemon=True,
    )
    consumer.start()

    rpt = max(1, int(args.request_per_tenant))
    log(f"request_per_tenant={rpt}  total_worker_threads={rpt * len(tenants)}")

    threads = []
    for t in tenants:
        q_idxs, q_emb = tq[t]
        n = len(q_idxs)
        for j in range(rpt):
            # Space starting offsets evenly so parallel workers for the same
            # tenant do not all hammer the same first queries at wall-clock t0.
            start_pos = (j * n) // rpt if rpt > 1 else 0
            th = threading.Thread(
                target=tenant_worker,
                args=(client, t, args.collection, q_idxs, q_emb,
                      args.duration, args.top_k, args.ef,
                      workers_stop, recall_q, counters[t], start_wall,
                      start_pos),
                name=f"tenant-{t}-w{j}",
            )
            threads.append(th)
            th.start()

    for th in threads:
        th.join()
    # Drain remaining queued search results before computing final recall.
    log(f"Search workers done; draining recall queue ({recall_q.qsize()} items) ...")
    # Wait for queue to fully drain
    while not recall_q.empty():
        time.sleep(0.05)
    consumer_stop.set()
    consumer.join(timeout=2.0)
    monitor_stop.set()
    mon.join(timeout=args.interval + 1.0)
    total = time.time() - start_wall

    # --- summary ---
    log(f"\n=== Benchmark complete in {total:.1f}s ===")
    for t in tenants:
        recs = records[t]
        n = len(recs)
        if n == 0:
            log(f"{t}: no completions")
            continue
        lats = np.array([r[2] for r in recs])
        total_hits = sum(r[3] for r in recs)
        recall = total_hits / (n * args.top_k)
        qps = n / total
        log(
            f"{t}: n={n:,}  QPS={qps:7.1f}  recall@{args.top_k}={recall:.4f}  "
            f"lat ms p50={np.percentile(lats,50):6.2f}  "
            f"p95={np.percentile(lats,95):6.2f}  "
            f"p99={np.percentile(lats,99):6.2f}  "
            f"max={lats.max():6.2f}"
        )

    # --- persist CSVs ---
    # Per-query recall for every completed search. One row = one query.
    per_query_csv = BENCH_DIR / f"per_query_recall_{tag}.csv"
    with per_query_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["tenant", "t_rel_s", "q_idx", "latency_ms", "hits",
                    f"recall@{args.top_k}"])
        for t in tenants:
            for rel, qid, lat, hits in records[t]:
                w.writerow([t, f"{rel:.6f}", qid, f"{lat:.4f}", hits,
                            f"{hits / args.top_k:.4f}"])
    log(f"Wrote {per_query_csv}")

    # Interval throughput + cumulative recall snapshot at each interval tick.
    thru_csv = BENCH_DIR / f"throughput_{tag}.csv"
    with thru_csv.open("w", newline="") as f:
        w = csv.writer(f)
        header = ["t_rel_s", "dt_s"]
        for t in tenants:
            header += [f"qps_{t}", f"cum_recall_{t}", f"completed_{t}", f"total_{t}"]
        w.writerow(header)
        for row in interval_rows:
            vals = [f"{row['t_rel_s']:.3f}", f"{row['dt_s']:.3f}"]
            for t in tenants:
                vals += [
                    f"{row.get(f'qps_{t}', 0.0):.3f}",
                    f"{row.get(f'cum_recall_{t}', float('nan')):.4f}",
                    int(row.get(f"completed_{t}", 0)),
                    int(row.get(f"total_{t}", 0)),
                ]
            w.writerow(vals)
    log(f"Wrote {thru_csv}")
    log(f"Wrote {log_path}")
    log_fh.close()


if __name__ == "__main__":
    main()
