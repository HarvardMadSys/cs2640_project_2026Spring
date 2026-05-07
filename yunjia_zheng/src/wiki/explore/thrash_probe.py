"""Run one probe, measure cache thrashing.

T1 counters sampled at 1 Hz from:
  /proc/<milvus_pid>/{stat,io,status}
  /sys/fs/cgroup<milvus_scope>/memory.stat

T2 mincore residency sampled at 0.5 Hz on a sampled set of Milvus-mapped
segment files under /scratch/yunjia/milvus-data/data/cache/8/local_chunk.
Each sampled file is mmapped read-only in this process, mincore() queried,
then unmapped. mincore reports kernel page-cache residency, which is a
process-independent view; our mmap adds a refcount but does not prefetch
or pin pages, so it does not perturb the measurement.

Output under explore/out/:
  thrash_<variant>_<probe>.json         summary deltas + per-probe stats
  thrash_<variant>_<probe>_t1.csv       per-sample counter values
  thrash_<variant>_<probe>_t2.csv       per-sample per-file residency
  thrash_<variant>_<probe>_latency.csv  per-sample latencies

Example:
  python thrash_probe.py --variant wiki_QC08_SS08 --probe CONC_BOTH \
      --warmup 30 --measure 30 --rpt 8 --nfiles 24
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import json
import mmap
import os
import random
import re
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from pymilvus import MilvusClient, connections, utility

from per_partition_cost import ensure_only_loaded

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)

MILVUS_DATA = Path("/scratch/yunjia/milvus-data/data/cache/8/local_chunk")
PAGESIZE = os.sysconf("SC_PAGE_SIZE")

PROBES = {
    "SOLO_SHARED":   [("A", ["shared_partition"])],
    "SOLO_PRIVATE":  [("A", ["partition_A"])],
    "SOLO_BOTH":     [("A", ["shared_partition", "partition_A"])],
    "CONC_SHARED":   [("A", ["shared_partition"]),
                      ("B", ["shared_partition"])],
    "CONC_PRIVATE":  [("A", ["partition_A"]),
                      ("B", ["partition_B"])],
    "CONC_BOTH":     [("A", ["shared_partition", "partition_A"]),
                      ("B", ["shared_partition", "partition_B"])],
}

PARTITION_OF_TENANT = {"A": "partition_A", "B": "partition_B"}


def load_queries(variant):
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


def find_milvus_pid():
    r = subprocess.run(["pgrep", "-xf",
                        "/scratch/yunjia/milvus/./bin/milvus run standalone"],
                       capture_output=True, text=True)
    pids = [int(x) for x in r.stdout.strip().splitlines() if x.strip()]
    if not pids:
        r = subprocess.run(["pgrep", "-f", "bin/milvus run standalone"],
                           capture_output=True, text=True)
        pids = [int(x) for x in r.stdout.strip().splitlines() if x.strip()]
    if not pids:
        raise RuntimeError("Milvus pid not found")
    return min(pids)


def cgroup_path(pid):
    with open(f"/proc/{pid}/cgroup") as f:
        for line in f:
            line = line.strip()
            if line.startswith("0::"):
                return "/sys/fs/cgroup" + line[3:]
    return None


def read_counters(pid, cg):
    out = {"t": time.time()}
    try:
        with open(f"/proc/{pid}/stat") as f:
            line = f.read()
        i = line.rfind(")")
        parts = line[i + 2:].split()
        out["minflt"] = int(parts[7])
        out["majflt"] = int(parts[9])
    except Exception:
        pass
    try:
        with open(f"/proc/{pid}/io") as f:
            for line in f:
                k, v = line.split(":", 1)
                if k.strip() in ("read_bytes", "rchar", "write_bytes", "wchar"):
                    out[k.strip()] = int(v.strip())
    except Exception:
        pass
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                for key in ("VmRSS:", "RssFile:", "RssAnon:"):
                    if line.startswith(key):
                        out[key.rstrip(":")] = int(line.split()[1])
    except Exception:
        pass
    if cg:
        try:
            with open(f"{cg}/memory.stat") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) != 2:
                        continue
                    k, v = parts
                    if k in ("pgfault", "pgmajfault",
                             "workingset_refault_file",
                             "workingset_activate_file",
                             "workingset_restore_file",
                             "file", "anon"):
                        out[f"cg_{k}"] = int(v)
        except Exception:
            pass
        try:
            with open(f"{cg}/memory.current") as f:
                out["cg_memory_current"] = int(f.read().strip())
        except Exception:
            pass
    return out


def t1_sampler(pid, cg, stop_evt, rows: list, period=1.0):
    while not stop_evt.wait(period):
        rows.append(read_counters(pid, cg))


libc = ctypes.CDLL("libc.so.6", use_errno=True)
libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
                      ctypes.c_int, ctypes.c_int, ctypes.c_long]
libc.mmap.restype = ctypes.c_void_p
libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
libc.munmap.restype = ctypes.c_int
libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t,
                         ctypes.POINTER(ctypes.c_ubyte)]
libc.mincore.restype = ctypes.c_int
PROT_READ = 0x1
MAP_SHARED = 0x01
MAP_FAILED = ctypes.c_void_p(-1).value


def file_residency(path):
    try:
        fd = os.open(path, os.O_RDONLY)
    except Exception:
        return 0, 0
    try:
        size = os.fstat(fd).st_size
        if size == 0:
            return 0, 0
        addr = libc.mmap(None, size, PROT_READ, MAP_SHARED, fd, 0)
        if addr is None or addr == MAP_FAILED:
            return size, 0
        try:
            npages = (size + PAGESIZE - 1) // PAGESIZE
            vec = (ctypes.c_ubyte * npages)()
            if libc.mincore(addr, size, vec) != 0:
                return size, 0
            res_pages = sum(1 for b in vec if b & 1)
            return size, res_pages * PAGESIZE
        finally:
            libc.munmap(addr, size)
    finally:
        os.close(fd)


SEG_RE = re.compile(r"/seg_(\d+)_cg_")
INDEX_RE = re.compile(r"/index_files/(\d+)/(\d+)/\d+/index$")


def discover_segment_files(pid):
    """Return (chunks, indexes) where
      chunks: dict segment_id -> list of seg_*_cg_* file paths
      indexes: dict collection_id -> list of (build_id, path) for big HNSW index files
    All paths are files Milvus has mmapped (i.e. currently in its address space).
    """
    chunks = defaultdict(list)
    indexes = defaultdict(list)
    base = str(MILVUS_DATA)
    seen_paths = set()
    with open(f"/proc/{pid}/maps") as f:
        for line in f:
            parts = line.split(maxsplit=5)
            if len(parts) < 6:
                continue
            path = parts[5].strip()
            if path in seen_paths or not path.startswith(base):
                continue
            seen_paths.add(path)
            m = SEG_RE.search(path)
            if m:
                chunks[int(m.group(1))].append(path)
                continue
            m2 = INDEX_RE.search(path)
            if m2:
                coll_id = int(m2.group(1))
                build_id = int(m2.group(2))
                indexes[coll_id].append((build_id, path))
    return chunks, indexes


VARIANT_COLL_ID = {
    "wiki_QC02_SS02": 465776990003887796,
    "wiki_QC02_SS08": 465776990039944662,
    "wiki_QC08_SS08": 465552462429522983,
}


def partition_of_segments(variant):
    """Use Milvus: segment_id -> partition_id, partition_id -> friendly name."""
    connections.connect(uri="http://localhost:19530", alias="thrashprobe")
    seg_infos = utility.get_query_segment_info(variant,
                                               using="thrashprobe")
    client = MilvusClient(uri="http://localhost:19530")
    partition_names = client.list_partitions(variant)
    # Milvus SDK get_partition_stats doesn't give partition_id directly; we
    # rely on the ordering convention (shared_partition created first, then A,
    # then B).  We also extract the id range via get_partition_info if
    # possible; fall back to listing via describe_collection.
    # Simpler: seg_infos already has partitionID; we just need a mapping from
    # partition_id -> readable label, derived from the partition sizes we
    # know match the meta.
    import json as _json
    meta = _json.loads((ROOT / f"{variant}_info" / "meta.json").read_text())
    want = {
        "shared_partition": int(meta["final_shared_size"]),
        "partition_A":      int(meta["partition_A_size"]),
        "partition_B":      int(meta["partition_B_size"]),
    }
    rows_by_pid = defaultdict(int)
    for s in seg_infos:
        rows_by_pid[s.partitionID] += s.num_rows
    # label partitions by matching row counts (tolerate tiny mismatch)
    pid_to_label = {}
    for pid, total in rows_by_pid.items():
        closest = min(want, key=lambda k: abs(want[k] - total))
        if abs(want[closest] - total) / max(want[closest], 1) < 0.05:
            pid_to_label[pid] = closest
    seg_to_label = {}
    for s in seg_infos:
        lbl = pid_to_label.get(s.partitionID)
        if lbl:
            seg_to_label[s.segmentID] = lbl
    return seg_to_label


def pick_files(chunks, indexes, seg_to_label, coll_id,
               partitions_wanted, n_chunks, n_indexes):
    """Balanced sample: n_chunks small chunk files and n_indexes big HNSW
    index files.

    Chunks carry a partition label when possible (seg id lookup). Index
    files are labelled `hnsw_index_<coll>` because the on-disk tree does
    not expose the partition of a build_id directly, so we cannot
    attribute them to shared vs private without querying Milvus's meta.
    """
    rnd = random.Random(42)

    by_label = defaultdict(list)
    for seg_id, paths in chunks.items():
        lbl = seg_to_label.get(seg_id)
        if lbl in partitions_wanted:
            for p in paths:
                by_label[lbl].append((seg_id, p))
    picked = []
    per = max(n_chunks // max(len(partitions_wanted), 1), 1)
    for lbl in partitions_wanted:
        lst = by_label.get(lbl, [])
        rnd.shuffle(lst)
        for seg_id, path in lst[:per]:
            picked.append((lbl, seg_id, path))

    idx_list = indexes.get(coll_id, [])
    rnd.shuffle(idx_list)
    for build_id, path in idx_list[:n_indexes]:
        picked.append(("hnsw_index", build_id, path))
    return picked


def t2_sampler(files, stop_evt, rows: list, period=2.0):
    while not stop_evt.wait(period):
        t = time.time()
        for lbl, seg_id, p in files:
            size, resident = file_residency(p)
            rows.append((t, lbl, seg_id, p, size, resident))


def worker_loop(client, collection, partitions, q_emb, ef, top_k,
                stop_evt, record, start_pos):
    params = {"metric_type": "COSINE", "params": {"ef": ef}}
    q = q_emb.tolist()
    n = len(q)
    i = start_pos
    while not stop_evt.is_set():
        t0 = time.perf_counter()
        client.search(
            collection_name=collection,
            data=[q[i % n]],
            anns_field="vector",
            search_params=params,
            limit=top_k,
            partition_names=partitions,
        )
        record((time.perf_counter() - t0) * 1000.0)
        i += 1


def run_phased(client, collection, cfg, warmup, measure, ef, top_k,
               checkpoint=None, checkpoint_every=10.0):
    """Run the probe with periodic checkpoints during warmup and measure.

    `checkpoint(phase_name, samples, ts)` is invoked every `checkpoint_every`
    seconds while the workers are running so the caller can flush partial
    results to disk. `phase_name` is "warmup" or "measure".
    """
    stop_evt = threading.Event()
    record_on = [False]
    samples: dict = defaultdict(list)
    locks = defaultdict(threading.Lock)

    def make_rec(label):
        def rec(lat_ms):
            if record_on[0]:
                with locks[label]:
                    samples[label].append((time.time(), lat_ms))
        return rec

    ts = {"warmup_start": time.time()}
    threads = []
    for tenant_label, parts, emb, rpt in cfg:
        n = len(emb)
        for j in range(rpt):
            start_pos = (j * n) // max(rpt, 1)
            th = threading.Thread(
                target=worker_loop,
                args=(client, collection, parts, emb, ef, top_k,
                      stop_evt, make_rec(tenant_label), start_pos),
                daemon=True,
                name=f"{tenant_label}-w{j}",
            )
            threads.append(th)
            th.start()

    def chunked_sleep(end_t, phase):
        while True:
            now = time.time()
            if now >= end_t:
                break
            time.sleep(min(checkpoint_every, max(end_t - now, 0.01)))
            if checkpoint is not None:
                try:
                    checkpoint(phase, samples, ts)
                except Exception as e:
                    print(f"  checkpoint({phase}) failed: {e}")

    chunked_sleep(ts["warmup_start"] + warmup, "warmup")
    ts["measure_start"] = time.time()
    record_on[0] = True
    chunked_sleep(ts["measure_start"] + measure, "measure")
    ts["measure_end"] = time.time()
    stop_evt.set()
    for th in threads:
        th.join(timeout=10.0)
    return samples, ts


def summarize_t1(rows, t_measure, t_end):
    if not rows:
        return {}
    # find sample closest to t_measure (first after) and closest to t_end
    # (last before).
    before = [r for r in rows if r["t"] <= t_measure]
    after = [r for r in rows if r["t"] >= t_end]
    within = [r for r in rows if t_measure <= r["t"] <= t_end]
    if not within or len(within) < 2:
        return {}
    a, b = within[0], within[-1]
    dur = b["t"] - a["t"]
    delta = {}
    for k in a:
        if k in ("t",):
            continue
        if k in b and isinstance(a[k], (int, float)) and isinstance(b[k], (int, float)):
            delta[k] = b[k] - a[k]
    delta["duration_s"] = dur
    return delta


def summarize_t2(rows):
    if not rows:
        return {}
    by_path = defaultdict(list)
    for t, lbl, seg, p, size, res in rows:
        by_path[(lbl, seg, p, size)].append((t, res))
    per_file = []
    for (lbl, seg, p, size), ts in by_path.items():
        ts.sort()
        res = [x[1] for x in ts]
        churn = sum(abs(res[i + 1] - res[i]) for i in range(len(res) - 1))
        per_file.append(dict(
            label=lbl, seg=seg, path=p, size=size,
            res_start=res[0], res_end=res[-1],
            res_min=min(res), res_max=max(res),
            res_mean=sum(res) / len(res),
            churn_bytes=churn,
        ))
    per_file.sort(key=lambda r: -r["churn_bytes"])
    # Aggregate by label.
    by_lbl = defaultdict(lambda: dict(
        size=0, res_start=0, res_end=0, res_min=0, res_max=0, churn=0, n=0,
    ))
    for r in per_file:
        d = by_lbl[r["label"]]
        d["size"] += r["size"]
        d["res_start"] += r["res_start"]
        d["res_end"] += r["res_end"]
        d["res_min"] += r["res_min"]
        d["res_max"] += r["res_max"]
        d["churn"] += r["churn_bytes"]
        d["n"] += 1
    return {"per_file_top": per_file[:20], "by_label": dict(by_lbl)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True,
                    choices=["wiki_QC02_SS02", "wiki_QC02_SS08", "wiki_QC08_SS08"])
    ap.add_argument("--probe", required=True, choices=list(PROBES))
    ap.add_argument("--warmup", type=float, default=30.0)
    ap.add_argument("--measure", type=float, default=30.0)
    ap.add_argument("--rpt", type=int, default=8)
    ap.add_argument("--ef", type=int, default=200)
    ap.add_argument("--top-k", type=int, default=100)
    ap.add_argument("--n-chunks", type=int, default=16,
                    help="number of small seg_*_cg_* chunk files to sample")
    ap.add_argument("--n-indexes", type=int, default=20,
                    help="number of big HNSW index files to sample")
    ap.add_argument("--t1-period", type=float, default=1.0)
    ap.add_argument("--t2-period", type=float, default=2.0)
    ap.add_argument("--uri", default="http://localhost:19530")
    args = ap.parse_args()

    collection = args.variant
    pid = find_milvus_pid()
    cg = cgroup_path(pid)
    print(f"Milvus pid={pid}  cgroup={cg}")

    client = MilvusClient(uri=args.uri)
    ensure_only_loaded(client, collection)
    q = load_queries(args.variant)

    # Which partitions this probe actually touches?
    parts_wanted = set()
    for tenant_label, parts in PROBES[args.probe]:
        parts_wanted.update(parts)
    print(f"Probe {args.probe} touches partitions: {sorted(parts_wanted)}")

    # Build workers cfg with embeddings.
    cfg = []
    for tenant_label, parts in PROBES[args.probe]:
        cfg.append((tenant_label, parts, q[tenant_label], args.rpt))

    # Warm QueryNode with one search per tenant/partition scope.
    params = {"metric_type": "COSINE", "params": {"ef": args.ef}}
    for tenant_label, parts in PROBES[args.probe]:
        client.search(
            collection_name=collection,
            data=[q[tenant_label][0].tolist()],
            anns_field="vector", search_params=params, limit=args.top_k,
            partition_names=parts,
        )

    # Discover mapped segment files and map to partition labels.
    chunks, indexes = discover_segment_files(pid)
    seg_to_label = partition_of_segments(args.variant)
    coll_id = VARIANT_COLL_ID[args.variant]
    files = pick_files(chunks, indexes, seg_to_label, coll_id,
                       parts_wanted, args.n_chunks, args.n_indexes)
    lbls = sorted(set(f[0] for f in files))
    print(f"Sampled {len(files)} files; labels={lbls}")

    # Start samplers.
    t1_rows: list = []
    t2_rows: list = []
    stop_t1 = threading.Event()
    stop_t2 = threading.Event()
    thr_t1 = threading.Thread(
        target=t1_sampler, args=(pid, cg, stop_t1, t1_rows, args.t1_period),
        daemon=True)
    thr_t2 = threading.Thread(
        target=t2_sampler, args=(files, stop_t2, t2_rows, args.t2_period),
        daemon=True)
    thr_t1.start()
    thr_t2.start()

    tag = f"{args.variant}_{args.probe}"
    json_path = OUT / f"thrash_{tag}.json"
    t1_csv = OUT / f"thrash_{tag}_t1.csv"
    t2_csv = OUT / f"thrash_{tag}_t2.csv"
    lat_csv = OUT / f"thrash_{tag}_latency.csv"

    def atomic_write(path, write_fn):
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", newline="") as f:
            write_fn(f)
        os.replace(tmp, path)

    def flush(latencies, ts, status):
        """Snapshot all output files. Safe to call any time during the run."""
        # CSV: T1 sampler rows
        if t1_rows:
            keys = sorted(set().union(*[r.keys() for r in t1_rows]))
            def _w_t1(f):
                w = csv.DictWriter(f, fieldnames=keys)
                w.writeheader()
                w.writerows(list(t1_rows))
            atomic_write(t1_csv, _w_t1)
        # CSV: T2 mincore rows
        def _w_t2(f):
            w = csv.writer(f)
            w.writerow(["t", "label", "seg", "path",
                        "size_bytes", "resident_bytes"])
            w.writerows(list(t2_rows))
        atomic_write(t2_csv, _w_t2)
        # CSV: latency rows
        def _w_lat(f):
            w = csv.writer(f)
            w.writerow(["tenant", "t_abs", "latency_ms"])
            for tenant_label, arr in latencies.items():
                for t, l in list(arr):
                    w.writerow([tenant_label, f"{t:.3f}", f"{l:.3f}"])
        atomic_write(lat_csv, _w_lat)

        # JSON: best-effort summary against whatever we have so far.
        t_meas_start = ts.get("measure_start")
        t_meas_end = ts.get("measure_end") or time.time()
        measure_dur = max((t_meas_end - t_meas_start) if t_meas_start else 0,
                          1e-6)
        tenant_stats = {}
        for tenant_label, _parts in PROBES[args.probe]:
            lst = latencies.get(tenant_label, []) if t_meas_start else []
            if not lst:
                tenant_stats[tenant_label] = dict(n=0)
                continue
            lats = [x[1] for x in lst]
            tenant_stats[tenant_label] = dict(
                n=len(lst),
                qps=len(lst) / measure_dur,
                per_worker_qps=len(lst) / measure_dur / args.rpt,
                p50=float(np.percentile(lats, 50)),
                p95=float(np.percentile(lats, 95)),
                p99=float(np.percentile(lats, 99)),
            )
        t1_delta = (summarize_t1(list(t1_rows), t_meas_start, t_meas_end)
                    if t_meas_start else {})
        total_queries = sum(v.get("n", 0) for v in tenant_stats.values())
        t1_per_query = {}
        if total_queries > 0 and t1_delta:
            for k, v in t1_delta.items():
                if k == "duration_s":
                    continue
                if isinstance(v, (int, float)):
                    t1_per_query[k] = v / total_queries
        t2_summary = summarize_t2(list(t2_rows))

        summary = dict(
            args=vars(args),
            probe=args.probe,
            status=status,
            phase_ts=ts,
            measure_duration_s=measure_dur if t_meas_start else 0.0,
            tenant_stats=tenant_stats,
            t1_delta=t1_delta,
            t1_per_query=t1_per_query,
            t2=t2_summary,
            n_files_sampled=len(files),
        )
        atomic_write(json_path,
                     lambda f: json.dump(summary, f, indent=2, default=str))
        return summary, total_queries

    # Touch the output files so the user can `tail` them straight away.
    flush({}, {"warmup_start": time.time()}, status="starting")
    print(f"  outputs: {json_path}, {t1_csv}, {t2_csv}, {lat_csv}")

    print(f"Running probe {args.probe} on {collection}: "
          f"warmup={args.warmup}s measure={args.measure}s rpt={args.rpt}")

    def checkpoint(phase, samples, ts):
        flush(samples, ts, status=f"in_progress_{phase}")
        n = sum(len(v) for v in samples.values())
        print(f"   [checkpoint {phase}] queries_so_far={n} "
              f"t1_rows={len(t1_rows)} t2_rows={len(t2_rows)}")

    latencies, ts = run_phased(client, collection, cfg,
                               args.warmup, args.measure,
                               args.ef, args.top_k,
                               checkpoint=checkpoint,
                               checkpoint_every=10.0)

    stop_t1.set()
    stop_t2.set()
    thr_t1.join(timeout=2.0)
    thr_t2.join(timeout=4.0)

    summary, total_queries = flush(latencies, ts, status="done")

    # Pretty-print the headline numbers.
    measure_dur = summary["measure_duration_s"]
    for tenant_label, st in summary["tenant_stats"].items():
        if st.get("n", 0) == 0:
            continue
        print(f"  {tenant_label}: n={st['n']} qps={st['qps']:.2f} "
              f"per_worker={st['per_worker_qps']:.2f} "
              f"p50={st['p50']:.1f}ms p95={st['p95']:.1f}ms")
    print("\n--- T1 counters over the measurement window ---")
    headline = ["majflt", "cg_pgmajfault",
                "cg_workingset_refault_file",
                "cg_workingset_activate_file",
                "read_bytes"]
    t1_delta = summary["t1_delta"]
    if t1_delta:
        for k in headline:
            if k in t1_delta:
                v = t1_delta[k]
                pq = v / max(total_queries, 1)
                print(f"  {k:32s}  total={v:>14,}  per_query={pq:>10,.1f}")
    print("\n--- T2 per-partition residency + churn (MB) ---")
    for lbl, s in summary["t2"].get("by_label", {}).items():
        print(f"  {lbl}: n_files={s['n']}  size={s['size']/1e6:.0f}MB  "
              f"res_start={s['res_start']/1e6:.0f}MB  "
              f"res_end={s['res_end']/1e6:.0f}MB  "
              f"churn={s['churn']/1e6:.0f}MB")
    print(f"\nWrote {json_path.name}, {t1_csv.name}, {t2_csv.name}, {lat_csv.name}")


if __name__ == "__main__":
    main()
