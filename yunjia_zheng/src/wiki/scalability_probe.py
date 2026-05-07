"""scalability_probe.py

Scalability sweep for the three compacted wiki variants. For each
variant we run tenant A's query workload against [shared_partition,
partition_A] under five thread counts and three thread-start
conditions:

  thread counts : 1, 4, 8, 16, 32
  start modes   :
    together     all threads start at the same instant
    stagger10ms  thread j issues its first request at j * 10 ms
    stagger50ms  thread j issues its first request at j * 50 ms

Each (threads, start_mode) run does:
  warmup    20 s with workers running, samples discarded
  measure   90 s with samples recorded
The reported qps is total recorded requests / measure duration.

Alongside end-to-end latency (client perf_counter around each search),
we sample two Milvus Prometheus histograms at the start and end of the
measure window to derive *server-side* execution time per query:

  milvus_querynode_segment_access_global_duration_{sum,count}{query_type="search"}
        sum_delta is the total time spent in segment accesses; sum_delta
        / n_queries_completed is the per-query server segment-access
        time. Unit is milliseconds (Milvus 2.x convention).
  milvus_cgo_cgo_duration_seconds_{sum,count}{name="search"}
        time spent inside the C++ search call as a whole; sum is in
        seconds (explicit in the metric name). We convert to ms.

Comparing server-side time vs end-to-end time across thread counts
isolates real on-server interference (cache/CPU contention) from
client-side queueing and network. If server time grows with thread
count, that's interference. If server time stays flat while end-to-end
grows, the extra latency is queueing.

Output:
  Working directory during a variant's sweep:
    /scratch/yunjia/milvus_experiments/wiki/scalability_result/
       results.json         all completed (threads, start_mode) rows
       results.csv          flat per-run rows for plotting
       summary.txt          per-run printout + headline table
  After all (5 thread counts) x (3 start modes) = 15 runs for a
  variant complete, the directory is renamed to
    scalability_result_<variant>/
  so the next variant gets a fresh scalability_result/ to write into.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from pymilvus import MilvusClient

ROOT = Path("/scratch/yunjia/milvus_experiments/wiki")
WORK_DIR = ROOT / "scalability_result"

VARIANTS = ["wiki_QC02_SS08","wiki_QC02_SS02", "wiki_QC08_SS08"]
THREAD_COUNTS = [1, 2, 4, 8, 16, 32]
START_MODES = ["together", "stagger10ms", "stagger50ms"]
START_INTERVAL_S = {
    "together": 0.0,
    "stagger10ms": 0.010,
    "stagger50ms": 0.050,
}
PARTS = ["shared_partition", "partition_A"]

_SUMMARY_LINES: list[str] = []


def tee(s: str = "") -> None:
    print(s)
    _SUMMARY_LINES.append(s)


PROM_URL = "http://localhost:9091/metrics"

# Server-side execution timing. Sampled at the start and end of the
# measure window so the delta tells us total server-side time spent on
# search work in the window. Averaged over the queries the workers
# completed in the same window, this gives per-query server execution
# time, independent of client-side queueing.
#
# segment_access_global_duration tracks one event per (query, segment)
# access. Its `_sum` is the total time spent across all segment
# accesses; unit is unlabelled in the metric name but is conventionally
# milliseconds in Milvus 2.x.
# cgo_duration_seconds tracks the time spent inside the cgo "search"
# call as a whole; `_sum` is in seconds (reliable unit).
_SEG_DUR_SUM_RE = re.compile(
    r'^milvus_querynode_segment_access_global_duration_sum\{[^}]*query_type="search"[^}]*\}\s+([0-9eE.+-]+)')
_SEG_DUR_CNT_RE = re.compile(
    r'^milvus_querynode_segment_access_global_duration_count\{[^}]*query_type="search"[^}]*\}\s+([0-9eE.+-]+)')
_CGO_DUR_SUM_RE = re.compile(
    r'^milvus_cgo_cgo_duration_seconds_sum\{[^}]*name="search"[^}]*\}\s+([0-9eE.+-]+)')
_CGO_DUR_CNT_RE = re.compile(
    r'^milvus_cgo_cgo_duration_seconds_count\{[^}]*name="search"[^}]*\}\s+([0-9eE.+-]+)')


def scrape_server_timing() -> dict | None:
    try:
        with urllib.request.urlopen(PROM_URL, timeout=2) as r:
            text = r.read().decode("utf-8", errors="ignore")
    except Exception:
        return None
    out = {"seg_dur_sum": 0.0, "seg_dur_count": 0.0,
           "cgo_dur_sum_s": 0.0, "cgo_dur_count": 0.0}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = _SEG_DUR_SUM_RE.match(line)
        if m:
            out["seg_dur_sum"] += float(m.group(1))
            continue
        m = _SEG_DUR_CNT_RE.match(line)
        if m:
            out["seg_dur_count"] += float(m.group(1))
            continue
        m = _CGO_DUR_SUM_RE.match(line)
        if m:
            out["cgo_dur_sum_s"] += float(m.group(1))
            continue
        m = _CGO_DUR_CNT_RE.match(line)
        if m:
            out["cgo_dur_count"] += float(m.group(1))
    return out


def load_a_queries(variant: str) -> np.ndarray:
    idir = ROOT / f"{variant}_info"
    tbl = pq.read_table(idir / "query_assignment.parquet")
    partitions = tbl.column("partition").to_pylist()
    q_idxs = tbl.column("q_idx").to_numpy()
    emb = np.load(idir / "query_embeddings.npy")
    sel = np.array([p == "A" for p in partitions], dtype=bool)
    return emb[q_idxs[sel]]


def ensure_only_loaded(client, target: str) -> None:
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


def run_one(client, variant, q_emb, threads, start_mode, args) -> dict:
    interval = START_INTERVAL_S[start_mode]
    params = {"metric_type": "COSINE", "params": {"ef": args.ef}}

    record_on = [False]
    samples: list[tuple[float, float]] = []
    samples_lock = threading.Lock()

    start_evt = threading.Event()
    stop_evt = threading.Event()
    n_emb = len(q_emb)
    q_list = q_emb.tolist()

    def worker(start_pos: int, start_delay: float):
        # All workers wait here so 'together' mode is a true simultaneous
        # start, then each one may sleep its stagger delay before issuing
        # its first request.
        start_evt.wait()
        if start_delay > 0:
            time.sleep(start_delay)
        i = start_pos
        while not stop_evt.is_set():
            t0 = time.perf_counter()
            client.search(
                collection_name=variant,
                data=[q_list[i % n_emb]],
                anns_field="vector",
                search_params=params,
                limit=args.top_k,
                partition_names=PARTS,
            )
            lat_ms = (time.perf_counter() - t0) * 1000.0
            if record_on[0]:
                with samples_lock:
                    samples.append((time.time(), lat_ms))
            i += 1

    workers: list[threading.Thread] = []
    for j in range(threads):
        start_pos = (j * n_emb) // max(threads, 1)
        delay = j * interval
        th = threading.Thread(
            target=worker, args=(start_pos, delay),
            daemon=True,
            name=f"{variant}-{start_mode}-t{threads}-w{j}",
        )
        workers.append(th)
        th.start()

    # Release all workers at the same instant.
    start_evt.set()

    time.sleep(args.warmup)
    record_on[0] = True
    server_pre = scrape_server_timing()
    t_start = time.time()
    time.sleep(args.measure)
    record_on[0] = False
    t_end = time.time()
    server_post = scrape_server_timing()

    stop_evt.set()
    for th in workers:
        th.join(timeout=10.0)

    measure_dur = max(t_end - t_start, 1e-6)
    lats = [s[1] for s in samples]
    n_q = len(lats)

    # Server-side execution timing from Prometheus. d_seg_dur is the
    # total time spent in segment accesses (assumed milliseconds, the
    # Milvus convention). d_cgo_dur_s is total time in the C++ search
    # call (seconds, explicit in the metric name). Dividing by n_q
    # gives a per-query server execution time that excludes client-side
    # queueing and network, so comparing across thread counts isolates
    # real on-server interference.
    d_seg_dur = d_seg_count = d_cgo_dur_s = d_cgo_count = 0.0
    if server_pre is not None and server_post is not None:
        d_seg_dur = server_post["seg_dur_sum"] - server_pre["seg_dur_sum"]
        d_seg_count = server_post["seg_dur_count"] - server_pre["seg_dur_count"]
        d_cgo_dur_s = server_post["cgo_dur_sum_s"] - server_pre["cgo_dur_sum_s"]
        d_cgo_count = server_post["cgo_dur_count"] - server_pre["cgo_dur_count"]

    seg_access_per_query = (d_seg_count / n_q) if n_q > 0 else None
    server_seg_dur_per_query_ms = (d_seg_dur / n_q) if n_q > 0 else None
    server_seg_dur_per_access_ms = (
        d_seg_dur / d_seg_count) if d_seg_count > 0 else None
    server_cgo_dur_per_query_ms = (
        (d_cgo_dur_s / n_q) * 1000.0) if n_q > 0 else None
    cgo_calls_per_query = (d_cgo_count / n_q) if n_q > 0 else None

    return dict(
        variant=variant,
        threads=threads,
        start_mode=start_mode,
        n=n_q,
        qps=n_q / measure_dur,
        per_thread_qps=(n_q / measure_dur / threads) if threads else 0.0,
        measure_duration_s=measure_dur,
        warmup_s=args.warmup,
        avg_latency_ms=(float(np.mean(lats)) if lats else 0.0),
        p50_latency_ms=(float(np.percentile(lats, 50)) if lats else 0.0),
        p95_latency_ms=(float(np.percentile(lats, 95)) if lats else 0.0),
        p99_latency_ms=(float(np.percentile(lats, 99)) if lats else 0.0),
        # Server-side execution timing.
        server_seg_dur_per_query_ms=server_seg_dur_per_query_ms,
        server_seg_dur_per_access_ms=server_seg_dur_per_access_ms,
        seg_access_per_query=seg_access_per_query,
        server_cgo_dur_per_query_ms=server_cgo_dur_per_query_ms,
        cgo_calls_per_query=cgo_calls_per_query,
        d_seg_dur=d_seg_dur,
        d_seg_count=d_seg_count,
        d_cgo_dur_s=d_cgo_dur_s,
        d_cgo_count=d_cgo_count,
    )


def write_results(work_dir: Path, results: list[dict]) -> None:
    json_path = work_dir / "results.json"
    csv_path = work_dir / "results.csv"
    txt_path = work_dir / "summary.txt"

    json_tmp = json_path.with_suffix(".json.tmp")
    with json_tmp.open("w") as f:
        json.dump(results, f, indent=2, default=str)
    os.replace(json_tmp, json_path)

    cols = ["variant", "threads", "start_mode", "n", "qps", "per_thread_qps",
            "measure_duration_s", "warmup_s",
            "avg_latency_ms", "p50_latency_ms",
            "p95_latency_ms", "p99_latency_ms",
            "server_seg_dur_per_query_ms",
            "server_seg_dur_per_access_ms",
            "seg_access_per_query",
            "server_cgo_dur_per_query_ms",
            "cgo_calls_per_query"]
    csv_tmp = csv_path.with_suffix(".csv.tmp")
    with csv_tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in results:
            w.writerow({k: r.get(k, "") for k in cols})
    os.replace(csv_tmp, csv_path)

    txt_tmp = txt_path.with_suffix(".txt.tmp")
    txt_tmp.write_text("\n".join(_SUMMARY_LINES) + "\n")
    os.replace(txt_tmp, txt_path)


def print_headline(results: list[dict]) -> None:
    """Headline pivot table: rows=start_mode, cols=threads, cells=qps."""
    by_mode_thr: dict[tuple[str, int], dict] = {
        (r["start_mode"], r["threads"]): r for r in results
    }
    modes = []
    for m in START_MODES:
        if any(r["start_mode"] == m for r in results):
            modes.append(m)
    threads = sorted({r["threads"] for r in results})

    tee("\n=== headline: qps ===")
    header = f"{'start_mode':<14}" + "".join(f" {t:>10}" for t in threads)
    tee(header)
    for m in modes:
        cells = []
        for t in threads:
            r = by_mode_thr.get((m, t))
            cells.append(f" {r['qps']:>10.2f}" if r else f" {'-':>10}")
        tee(f"{m:<14}" + "".join(cells))

    tee("\n=== headline: p95 end-to-end latency (ms) ===")
    tee(header)
    for m in modes:
        cells = []
        for t in threads:
            r = by_mode_thr.get((m, t))
            cells.append(f" {r['p95_latency_ms']:>10.1f}" if r else f" {'-':>10}")
        tee(f"{m:<14}" + "".join(cells))

    tee("\n=== headline: server seg-access time per query (ms) ===")
    tee(header)
    for m in modes:
        cells = []
        for t in threads:
            r = by_mode_thr.get((m, t))
            v = r.get("server_seg_dur_per_query_ms") if r else None
            cells.append(f" {v:>10.1f}" if v is not None else f" {'-':>10}")
        tee(f"{m:<14}" + "".join(cells))

    tee("\n=== headline: server cgo time per query (ms) ===")
    tee(header)
    for m in modes:
        cells = []
        for t in threads:
            r = by_mode_thr.get((m, t))
            v = r.get("server_cgo_dur_per_query_ms") if r else None
            cells.append(f" {v:>10.1f}" if v is not None else f" {'-':>10}")
        tee(f"{m:<14}" + "".join(cells))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--variants", nargs="+", default=VARIANTS)
    ap.add_argument("--threads", nargs="+", type=int, default=THREAD_COUNTS)
    ap.add_argument("--start-modes", nargs="+", default=START_MODES,
                    choices=START_MODES)
    ap.add_argument("--warmup", type=float, default=60.0)
    ap.add_argument("--measure", type=float, default=90.0)
    ap.add_argument("--ef", type=int, default=200)
    ap.add_argument("--top-k", type=int, default=100)
    ap.add_argument("--uri", default="http://localhost:19530")
    args = ap.parse_args()

    client = MilvusClient(uri=args.uri)

    for variant in args.variants:
        # Reset _SUMMARY_LINES so each variant's summary.txt only carries
        # its own runs, not previous variants'.
        _SUMMARY_LINES.clear()

        # Prep WORK_DIR. Wipe any prior contents from an interrupted run.
        if WORK_DIR.exists():
            print(f"  clearing prior {WORK_DIR}")
            shutil.rmtree(WORK_DIR)
        WORK_DIR.mkdir(parents=True)

        target_dir = ROOT / f"scalability_result_{variant}"
        if target_dir.exists():
            print(f"  ERROR: target {target_dir} already exists; "
                  f"move or delete it before re-running. Skipping {variant}.")
            continue

        try:
            q_a = load_a_queries(variant)
        except Exception as e:
            print(f"  load_a_queries({variant}) failed: {e}")
            continue

        ensure_only_loaded(client, variant)
        tee(f"\n==== {variant} ====")
        tee(f"  warmup={args.warmup}s measure={args.measure}s "
            f"ef={args.ef} k={args.top_k}")

        results: list[dict] = []
        for start_mode in args.start_modes:
            for threads in args.threads:
                tee(f"\n  -- {variant} :: {start_mode} :: threads={threads} --")
                summary = run_one(client, variant, q_a, threads,
                                  start_mode, args)
                results.append(summary)
                tee(f"     n={summary['n']:,} "
                    f"qps={summary['qps']:.2f} "
                    f"per_thread_qps={summary['per_thread_qps']:.2f}")
                tee(f"     end-to-end "
                    f"avg={summary['avg_latency_ms']:.1f}ms "
                    f"p50={summary['p50_latency_ms']:.1f}ms "
                    f"p95={summary['p95_latency_ms']:.1f}ms "
                    f"p99={summary['p99_latency_ms']:.1f}ms")
                seg_q = summary.get("server_seg_dur_per_query_ms")
                seg_a = summary.get("server_seg_dur_per_access_ms")
                seg_n = summary.get("seg_access_per_query")
                cgo_q = summary.get("server_cgo_dur_per_query_ms")
                seg_q_s = f"{seg_q:.1f}" if seg_q is not None else "n/a"
                seg_a_s = f"{seg_a:.3f}" if seg_a is not None else "n/a"
                seg_n_s = f"{seg_n:.1f}" if seg_n is not None else "n/a"
                cgo_q_s = f"{cgo_q:.1f}" if cgo_q is not None else "n/a"
                tee(f"     server     "
                    f"seg/q={seg_q_s}ms "
                    f"per-access={seg_a_s}ms "
                    f"seg_accs/q={seg_n_s} "
                    f"cgo/q={cgo_q_s}ms")
                write_results(WORK_DIR, results)

        print_headline(results)
        write_results(WORK_DIR, results)

        # Rename WORK_DIR to scalability_result_<variant>/.
        WORK_DIR.rename(target_dir)
        print(f"  renamed {WORK_DIR.name} -> {target_dir.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
