"""Measure page-cache miss ratio and refault rate across the three
compaction variants.

For each variant in {wiki_QC02_SS02, wiki_QC02_SS08, wiki_QC08_SS08} we
do TWO runs back to back, each preceded by a fresh page-cache drop:

  AB8   A and B concurrently, 8 threads each (matches the bench setup)
  A16   A solo, 16 threads (same total worker count as AB8)

The variant is loaded exactly once via `ensure_only_loaded` (releasing
every other Milvus collection so the cgroup page cache is not split);
each run inside that variant drops the OS page cache, reissues
load_collection, warms up by running its own workers for --warmup s
(samples discarded), then measures for --measure s while sampling
/proc/<pid>/{stat,io} and the cgroup memory.stat at 1 Hz.

Counters used:
  /proc/<pid>/stat                minflt, majflt
  /proc/<pid>/io                  read_bytes (disk reads), rchar
  cgroup memory.stat              pgfault, pgmajfault,
                                  workingset_refault_file,
                                  workingset_activate_file,
                                  workingset_restore_file

Reported per variant:
  qps_A, qps_B, total_queries
  pgmajfault_per_query
  pgfault_per_query
  miss_ratio_pgmajfault_over_pgfault    (cgroup-level approximation of
                                         page-cache miss probability)
  refault_rate_per_s                    (workingset_refault_file / s)
  refault_rate_per_query
  read_bytes_per_query                  (bytes brought in from disk)

Output (under /scratch/yunjia/milvus_experiments/wiki/cache_behavior/),
where <run> is one of {AB8, A16}:
  cache_miss_probe.json
        per-(variant, run) summary: avg miss ratio, total refault, qps,
        latency p50/p95/p99 per tenant, raw counter deltas.
  cache_miss_probe_<variant>_<run>.csv
        raw 1 Hz counter samples taken during the measure window.
  cache_miss_probe_<variant>_<run>_interval.csv
        per-second interval miss ratio and refault rate, derived from
        the deltas between successive samples.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import signal
import subprocess
import threading
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from pymilvus import MilvusClient

ROOT = Path("/scratch/yunjia/milvus_experiments/wiki")
OUT = ROOT / "cache_behavior"
OUT.mkdir(parents=True, exist_ok=True)

# Lines that should appear in cache_behavior/summary.txt: every per-run
# block plus the headline. Populated via tee(); written by flush() so
# partial runs are preserved if the script crashes.
_SUMMARY_LINES: list[str] = []


def tee(s: str = "") -> None:
    print(s)
    _SUMMARY_LINES.append(s)

VARIANTS = ["wiki_QC02_SS02","wiki_QC02_SS08", "wiki_QC08_SS08"]

PARTITIONS_FOR = {
    "A": ["shared_partition", "partition_A"],
    "B": ["shared_partition", "partition_B"],
}

# Two measurement runs per variant. Each entry is (tenant_label,
# parts_key, threads). `run_tag` selects the workload shape:
#   AB8  -> A and B concurrently, 8 threads each (matches the bench)
#   A16  -> A solo, 16 threads (same total worker count as AB8)
RUNS = [
    # ("AB8", [("A", "A", 8), ("B", "B", 8)]),
    # ("A16", [("A", "A", 16)]),
    ("A1", [("A", "A", 1)]),
    ("A2", [("A", "A", 2)]),
    ("A4", [("A", "A", 4)]),
    ("A8", [("A", "A", 8)]),
]


def find_milvus_pid() -> int:
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


def cgroup_path(pid: int) -> str | None:
    with open(f"/proc/{pid}/cgroup") as f:
        for line in f:
            line = line.strip()
            if line.startswith("0::"):
                return "/sys/fs/cgroup" + line[3:]
    return None


def snapshot_numa_and_cgroup(pid: int, cg: str | None,
                             label: str = "snapshot") -> None:
    """Tee a small block describing NUMA topology, Milvus's memory
    placement per NUMA node, and the cgroup's memory pressure state.

    Pure /proc + /sys reads, no external tools needed.
    """
    PAGE_SIZE = 4096

    tee(f"\n  -- {label}: NUMA + cgroup --")

    # System NUMA topology.
    sys_node_dir = Path("/sys/devices/system/node")
    nodes: list[tuple[int, int, int]] = []
    if sys_node_dir.exists():
        for d in sorted(sys_node_dir.glob("node[0-9]*")):
            try:
                nid = int(d.name[4:])
                total_kb = free_kb = -1
                with (d / "meminfo").open() as f:
                    for ln in f:
                        if "MemTotal:" in ln:
                            total_kb = int(ln.split()[-2])
                        elif "MemFree:" in ln:
                            free_kb = int(ln.split()[-2])
                nodes.append((nid, total_kb, free_kb))
            except Exception:
                pass

    if nodes:
        parts = [f"node{n}: total={t/1e6:.1f}GB free={f/1e6:.1f}GB"
                 for n, t, f in nodes]
        tee("    topology: " + " | ".join(parts))
    else:
        tee("    topology: (single node or /sys/devices/system/node missing)")

    # Process binding.
    try:
        with open(f"/proc/{pid}/status") as f:
            cpus = mems = ""
            for ln in f:
                if ln.startswith("Cpus_allowed_list:"):
                    cpus = ln.split(":", 1)[1].strip()
                elif ln.startswith("Mems_allowed_list:"):
                    mems = ln.split(":", 1)[1].strip()
            tee(f"    bindings: Cpus_allowed_list={cpus} "
                f"Mems_allowed_list={mems}")
    except Exception as e:
        tee(f"    bindings: (read failed: {e})")

    # Aggregate Milvus's memory by NUMA node from /proc/<pid>/numa_maps.
    # Each line contains tokens like 'N0=12345 N1=67890' giving pages
    # currently mapped on each node for that mmap region. We sum pages
    # per node across all regions.
    pages_by_node: dict[int, int] = defaultdict(int)
    total_pages = 0
    try:
        with open(f"/proc/{pid}/numa_maps") as f:
            for ln in f:
                for tok in ln.split():
                    if not tok.startswith("N") or "=" not in tok:
                        continue
                    head, _, val = tok.partition("=")
                    try:
                        nid = int(head[1:])
                        pages = int(val)
                    except ValueError:
                        continue
                    pages_by_node[nid] += pages
                    total_pages += pages
    except Exception as e:
        tee(f"    numa_maps: (read failed: {e})")
        return

    if total_pages > 0:
        gb_total = total_pages * PAGE_SIZE / 1e9
        tee(f"    milvus mmap: {gb_total:.1f}GB resident across nodes")
        for nid in sorted(pages_by_node):
            gb = pages_by_node[nid] * PAGE_SIZE / 1e9
            pct = pages_by_node[nid] / total_pages * 100
            tee(f"      node{nid}: {gb:.1f}GB ({pct:.1f}%)")

    # cgroup memory state. memory.current is the live footprint;
    # memory.peak is the high-water mark since reset; memory.max is
    # the cap. workingset_refault_file is the cumulative count of
    # pages that were evicted and brought back, the cleanest signal
    # of capacity pressure.
    if cg:
        def _read(name: str) -> str:
            try:
                return Path(f"{cg}/{name}").read_text().strip()
            except Exception:
                return "?"

        cur = _read("memory.current")
        peak = _read("memory.peak")
        mmax = _read("memory.max")
        tee(f"    cgroup: memory.current={int(cur)/1e9:.1f}GB "
            f"peak={int(peak)/1e9:.1f}GB "
            f"max={mmax if mmax == 'max' else f'{int(mmax)/1e9:.1f}GB'}")

        wsr_evt = "?"
        try:
            with open(f"{cg}/memory.stat") as f:
                vals = {}
                for ln in f:
                    parts = ln.split()
                    if len(parts) == 2:
                        vals[parts[0]] = parts[1]
            wsr_evt = vals.get("workingset_refault_file", "?")
            tee(f"    cgroup memory.stat: "
                f"file={int(vals.get('file', 0))/1e9:.1f}GB "
                f"anon={int(vals.get('anon', 0))/1e9:.1f}GB "
                f"workingset_refault_file={wsr_evt}")
        except Exception:
            pass


CG_KEYS = (
    "pgfault", "pgmajfault",
    "workingset_refault_file",
    "workingset_activate_file",
    "workingset_restore_file",
    "file", "anon",
)


def read_counters(pid: int, cg: str | None) -> dict:
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
            for ln in f:
                k, v = ln.split(":", 1)
                k = k.strip()
                if k in ("read_bytes", "rchar", "write_bytes", "wchar"):
                    out[k] = int(v.strip())
    except Exception:
        pass
    if cg:
        try:
            with open(f"{cg}/memory.stat") as f:
                for ln in f:
                    parts = ln.split()
                    if len(parts) != 2:
                        continue
                    k, v = parts
                    if k in CG_KEYS:
                        out[f"cg_{k}"] = int(v)
        except Exception:
            pass
        try:
            with open(f"{cg}/memory.current") as f:
                out["cg_memory_current"] = int(f.read().strip())
        except Exception:
            pass
    return out


PROM_URL = "http://localhost:9091/metrics"

# We only care about the search totals; query/upsert/idle types stay zero
# in this benchmark.
#
# milvus_querynode_segment_access_total (counter, labelled by db/rg) is
# the metric we'd ideally use, but Milvus drops some of its per-rg
# label series silently when traffic is idle. The histogram counter
# milvus_querynode_segment_access_global_duration_count is observed
# every time a segment is accessed for a search and is exposed
# unconditionally, so it's the reliable choice.
_SEG_ACCESS_RE = re.compile(
    r'^milvus_querynode_segment_access_global_duration_count\{[^}]*query_type="search"[^}]*\}\s+([0-9eE.+-]+)')
_SEG_ACCESS_DUR_RE = re.compile(
    r'^milvus_querynode_segment_access_global_duration_sum\{[^}]*query_type="search"[^}]*\}\s+([0-9eE.+-]+)')
_NQ_RE_TMPL = (r'^milvus_proxy_received_nq\{[^}]*'
               r'collection_name="__COLL__"[^}]*query_type="search"[^}]*\}\s+'
               r'([0-9eE.+-]+)')


def _scrape_prom(coll: str) -> dict:
    """Pull the few metrics we care about from Milvus's Prometheus endpoint."""
    out = {"t": time.time()}
    try:
        with urllib.request.urlopen(PROM_URL, timeout=2) as r:
            text = r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        out["_error"] = str(e)
        return out
    nq_re = re.compile(
        _NQ_RE_TMPL.replace("__COLL__", re.escape(coll)), re.M)
    seg_re = _SEG_ACCESS_RE
    seg_dur_re = _SEG_ACCESS_DUR_RE
    seg_total = 0.0
    seg_dur = 0.0
    nq_total = 0.0
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = seg_re.match(line)
        if m:
            seg_total += float(m.group(1))
            continue
        m = seg_dur_re.match(line)
        if m:
            seg_dur += float(m.group(1))
            continue
        m = nq_re.match(line)
        if m:
            nq_total += float(m.group(1))
    out["mv_segment_access_total"] = seg_total
    out["mv_segment_access_duration"] = seg_dur
    out["mv_received_nq"] = nq_total
    return out


def sampler(pid, cg, coll, stop_evt, rows, period=1.0):
    while not stop_evt.wait(period):
        r = read_counters(pid, cg)
        r.update(_scrape_prom(coll))
        rows.append(r)


# ----------------------------------------------------------------------
# perf stat sidecar. Used to read hardware dTLB events for the Milvus
# pid, which gives us a denominator that includes silent hits to
# already-mapped resident pages, something the cgroup fault counters
# cannot expose. Output format requested via -x ';' and -I <ms> looks
# like:
#   <t_rel>;<count>;<unit>;<event>;<run-time>;<percentage>;<note>
# We aggregate per-(t, event) into intervals and a totals dict.
# ----------------------------------------------------------------------
PERF_EVENTS = ["dTLB-loads", "dTLB-load-misses"]


def start_perf_stat(pid: int, interval_ms: int):
    cmd = [
        "sudo", "-n", "perf", "stat",
        "-x", ";",
        "-I", str(interval_ms),
        "-e", ",".join(PERF_EVENTS),
        "-p", str(pid),
    ]
    try:
        p = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True,
        )
        return p
    except FileNotFoundError as e:
        print(f"  perf not available: {e}")
        return None
    except Exception as e:
        print(f"  perf start failed: {e}")
        return None


def stop_perf_stat(p):
    if p is None:
        return {}, []
    try:
        p.send_signal(signal.SIGINT)
    except Exception:
        pass
    try:
        _, err = p.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            p.kill()
            _, err = p.communicate(timeout=2)
        except Exception:
            err = ""
    return parse_perf_csv(err or "")


def parse_perf_csv(text: str):
    """Returns (totals: dict[event]->int, intervals: list[dict])."""
    by_t: dict = {}
    totals: dict = defaultdict(int)
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(";")
        # interval-mode rows: t_rel; count; unit; event; ...
        if len(parts) < 4:
            continue
        try:
            t_rel = float(parts[0])
        except ValueError:
            continue
        cnt_s = parts[1].strip()
        if cnt_s in ("<not supported>", "<not counted>", ""):
            count = 0
        else:
            try:
                count = int(float(cnt_s))
            except ValueError:
                continue
        event = parts[3].strip()
        by_t.setdefault(t_rel, {})[event] = count
        totals[event] += count
    intervals = []
    for t in sorted(by_t):
        d = by_t[t]
        intervals.append({
            "t_rel": t,
            "dTLB_loads": d.get("dTLB-loads", 0),
            "dTLB_load_misses": d.get("dTLB-load-misses", 0),
        })
    return dict(totals), intervals


def load_queries(variant: str):
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
        tee(f"  drop_caches failed: {e}")
        return False


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


def run_one(client, variant, run_tag, run_def, q, args, pid, cg):
    """One measurement run: drop cache, warmup, measure.

    `run_def` is a list of (tenant_label, parts_key, threads). The caller
    is responsible for `ensure_only_loaded` once per variant before
    calling this.
    """
    tee(f"\n==== {variant} :: {run_tag} ====")

    tee("  dropping page cache ...")
    drop_ok = drop_caches()

    # Re-issue load_collection so Milvus marks segments as Loaded again
    # if drop_caches blew away any in-memory metadata. (load_collection is
    # idempotent.)
    client.load_collection(variant)

    cfg = [
        (tenant_label, PARTITIONS_FOR[parts_key], q[parts_key], rpt)
        for (tenant_label, parts_key, rpt) in run_def
    ]
    total_threads = sum(rpt for _, _, rpt in run_def)
    tee(f"  workload: {[(l, t) for l, _, t in run_def]} "
        f"(total threads={total_threads})")

    # Latency recorder; only records during measurement phase.
    record_on = [False]
    samples: dict = defaultdict(list)
    locks = defaultdict(threading.Lock)

    def make_rec(label):
        def rec(lat_ms):
            if record_on[0]:
                with locks[label]:
                    samples[label].append((time.time(), lat_ms))
        return rec

    stop_evt = threading.Event()
    threads = []
    for tenant_label, parts, emb, rpt in cfg:
        n = len(emb)
        for j in range(rpt):
            start_pos = (j * n) // max(rpt, 1)
            th = threading.Thread(
                target=worker_loop,
                args=(client, variant, parts, emb, args.ef, args.top_k,
                      stop_evt, make_rec(tenant_label), start_pos),
                daemon=True,
                name=f"{variant}-{tenant_label}-w{j}",
            )
            threads.append(th)
            th.start()

    # Warmup: workers run, no recording.
    tee(f"  warmup {args.warmup}s ...")
    time.sleep(args.warmup)

    # Start counter sampler at the beginning of the measure window.
    sampler_rows: list = []
    stop_sampler = threading.Event()
    thr_s = threading.Thread(
        target=sampler,
        args=(pid, cg, variant, stop_sampler, sampler_rows, args.sample_period),
        daemon=True,
    )

    # Start perf stat sidecar so that, over the same window we sample
    # cgroup counters, we also capture dTLB-loads / dTLB-load-misses for
    # the Milvus pid. Used to compute classic_miss_ratio = pgmajfault /
    # dTLB-load-misses, where the denominator includes silent hits.
    perf_proc = start_perf_stat(pid, int(args.sample_period * 1000))

    # Snapshot the counters *now* (start of measure) to get an exact delta.
    c_start = read_counters(pid, cg)
    c_start.update(_scrape_prom(variant))
    record_on[0] = True
    thr_s.start()
    tee(f"  measure {args.measure}s ...")
    time.sleep(args.measure)
    record_on[0] = False
    c_end = read_counters(pid, cg)
    c_end.update(_scrape_prom(variant))

    perf_totals, perf_intervals = stop_perf_stat(perf_proc)

    stop_sampler.set()
    stop_evt.set()
    thr_s.join(timeout=2.0)
    for th in threads:
        th.join(timeout=10.0)

    # Compute deltas.
    measure_dur = c_end["t"] - c_start["t"]
    delta = {}
    for k, v in c_start.items():
        if k == "t":
            continue
        if isinstance(v, (int, float)) and k in c_end:
            delta[k] = c_end[k] - v

    per_tenant = {}
    for tenant_label, _, _ in run_def:
        per_tenant[tenant_label] = len(samples.get(tenant_label, []))
    total_q = sum(per_tenant.values())

    def safe(k):
        return delta.get(k, 0)

    pgfault = safe("cg_pgfault")
    pgmajfault = safe("cg_pgmajfault")
    minfault = pgfault - pgmajfault
    refault = safe("cg_workingset_refault_file")
    activate = safe("cg_workingset_activate_file")
    restore = safe("cg_workingset_restore_file")
    read_bytes = safe("read_bytes")
    rchar = safe("rchar")
    seg_access = safe("mv_segment_access_total")
    seg_access_dur = safe("mv_segment_access_duration")
    mv_received_nq = safe("mv_received_nq")

    # avg_disk_fault_ratio = pgmajfault / pgfault. Of all fault events,
    # the fraction that required a disk read. Both numerator and
    # denominator come from the same cgroup fault stream.
    avg_disk_fault_ratio = (pgmajfault / pgfault) if pgfault else None
    # classic_miss_ratio ≈ pgmajfault / dTLB-load-misses. Of all
    # page-grain accesses (including silent hits to already-mapped
    # resident pages), the fraction that required disk. dTLB-load-misses
    # is the closest hardware-side proxy for unique page accesses: every
    # load that found the TLB cold counts, regardless of whether the
    # page-table walk later found a valid PTE. This denominator includes
    # the silent hits that pgfault misses.
    dTLB_loads = int(perf_totals.get("dTLB-loads", 0))
    dTLB_load_misses = int(perf_totals.get("dTLB-load-misses", 0))
    avg_classic_miss_ratio = ((pgmajfault / dTLB_load_misses)
                              if dTLB_load_misses > 0 else None)
    refault_ratio_over_majflt = (refault / pgmajfault) if pgmajfault else None

    def per_q(x):
        return x / total_q if total_q else None

    def per_s(x):
        return x / measure_dur if measure_dur > 0 else None

    def stat(lats):
        if not lats:
            return None
        return dict(
            p50=float(np.percentile(lats, 50)),
            p95=float(np.percentile(lats, 95)),
            p99=float(np.percentile(lats, 99)),
        )

    latency_per_tenant = {
        label: stat([x[1] for x in samples.get(label, [])])
        for label, _, _ in run_def
    }
    qps_per_tenant = {
        label: (per_tenant[label] / measure_dur if measure_dur else 0.0)
        for label, _, _ in run_def
    }

    # Interval rows: derive per-second miss ratio + refault rate between
    # consecutive sampler snapshots. The first interval is anchored at
    # c_start (taken right before the measurement window opens) so we
    # don't lose the leading second. perf_intervals are merged in by
    # index, since perf and the cgroup sampler both run at
    # args.sample_period.
    interval_rows = build_interval_rows(
        c_start, sampler_rows, perf_intervals=perf_intervals)

    summary = dict(
        variant=variant,
        run_tag=run_tag,
        run_def=[{"tenant": l, "parts": p, "threads": t}
                 for (l, p, t) in run_def],
        drop_cache_ok=drop_ok,
        measure_duration_s=measure_dur,
        n_per_tenant=per_tenant,
        total_queries=total_q,
        qps_per_tenant=qps_per_tenant,
        qps_total=total_q / measure_dur if measure_dur else 0.0,
        latency_per_tenant=latency_per_tenant,
        # Raw counter deltas across the measure window.
        delta=delta,
        # Headline averages over the whole window.
        avg_disk_fault_ratio=avg_disk_fault_ratio,
        avg_classic_miss_ratio=avg_classic_miss_ratio,
        total_refault=refault,
        total_pgfault=pgfault,
        total_pgmajfault=pgmajfault,
        total_minfault=minfault,
        total_activate=activate,
        total_restore=restore,
        # Hardware-side counters from perf stat over the same window.
        # Used to compute classic_miss_ratio (above).
        total_dTLB_loads=dTLB_loads,
        total_dTLB_load_misses=dTLB_load_misses,
        dTLB_loads_per_query=(dTLB_loads / total_q) if total_q else None,
        dTLB_load_misses_per_query=((dTLB_load_misses / total_q)
                                     if total_q else None),
        dTLB_miss_rate=((dTLB_load_misses / dTLB_loads)
                         if dTLB_loads > 0 else None),
        # Milvus-side counters scraped from :9091. mv_received_nq is the
        # delta of milvus_proxy_received_nq for this collection (≈ total
        # search calls; usually equals total_q since rpt threads each
        # send 1 nq per request). seg_access_per_query is segment
        # accesses per search request; useful as a Milvus-side denominator
        # for "how much data did we have to touch."
        mv_segment_access_total_delta=int(seg_access),
        mv_segment_access_duration_delta=seg_access_dur,
        mv_received_nq_delta=int(mv_received_nq),
        seg_access_per_query=(seg_access / total_q) if total_q else None,
        seg_access_per_s=per_s(seg_access),
        read_bytes_per_seg_access=(read_bytes / seg_access)
                                   if seg_access else None,
        pgmajfault_per_seg_access=(pgmajfault / seg_access)
                                   if seg_access else None,
        # Per-query / per-second breakdowns.
        pgfault_per_query=per_q(pgfault),
        pgmajfault_per_query=per_q(pgmajfault),
        refault_per_s=per_s(refault),
        refault_per_query=per_q(refault),
        refault_over_pgmajfault=refault_ratio_over_majflt,
        activate_per_s=per_s(activate),
        restore_per_s=per_s(restore),
        read_bytes_per_query=per_q(read_bytes),
        read_bytes_per_s=per_s(read_bytes),
        rchar_per_query=per_q(rchar),
        n_interval_rows=len(interval_rows),
    )
    return summary, sampler_rows, interval_rows


def write_csv(path: Path, rows: list):
    if not rows:
        return
    keys = sorted(set().union(*[r.keys() for r in rows]))
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def write_interval_csv(path: Path, rows: list):
    if not rows:
        return
    cols = [
        "t_rel_s", "interval_s",
        "d_pgfault", "d_pgmajfault", "d_pgminfault", "d_refault",
        "d_activate", "d_restore",
        "d_read_bytes", "d_minflt", "d_majflt",
        "d_seg_access", "d_received_nq",
        "d_dTLB_loads", "d_dTLB_load_misses",
        "interval_disk_fault_ratio",
        "interval_classic_miss_ratio",
        "interval_dTLB_miss_rate",
        "pgfault_per_s",
        "pgmajfault_per_s",
        "pgminfault_per_s",
        "refault_per_s",
        "read_bytes_per_s",
        "seg_access_per_s",
        "seg_access_per_nq",
        "dTLB_loads_per_s",
        "dTLB_load_misses_per_s",
    ]
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})
    os.replace(tmp, path)


def build_interval_rows(c_start: dict, sampler_rows: list,
                        perf_intervals: list | None = None) -> list:
    """Diff successive counter snapshots into per-interval rates.

    The first interval is from `c_start` (taken just before the measurement
    window opens) to sampler_rows[0]; subsequent intervals are between
    consecutive sampler rows.

    `perf_intervals` are merged by index. Both perf and the sampler run
    at the same nominal cadence so index-alignment is good enough.
    """
    if not sampler_rows:
        return []
    perf_intervals = perf_intervals or []
    seq = [c_start] + list(sampler_rows)
    t0 = seq[0]["t"]
    out = []
    for i, (prev, cur) in enumerate(zip(seq, seq[1:])):
        dt = cur["t"] - prev["t"]
        if dt <= 0:
            continue
        def d(k):
            a = prev.get(k)
            b = cur.get(k)
            if a is None or b is None:
                return 0
            return b - a
        d_pgfault = d("cg_pgfault")
        d_pgmajfault = d("cg_pgmajfault")
        d_pgminfault = d_pgfault - d_pgmajfault
        d_refault = d("cg_workingset_refault_file")
        d_seg_access = d("mv_segment_access_total")
        d_nq = d("mv_received_nq")
        disk_fault_ratio = (d_pgmajfault / d_pgfault) if d_pgfault > 0 else None
        # Pull the matching perf interval if available. perf's first
        # interval starts ~1s after we spawned it, so its index 0
        # roughly aligns with our interval index 0 too.
        if i < len(perf_intervals):
            d_dtlb_loads = perf_intervals[i]["dTLB_loads"]
            d_dtlb_load_misses = perf_intervals[i]["dTLB_load_misses"]
        else:
            d_dtlb_loads = 0
            d_dtlb_load_misses = 0
        classic = ((d_pgmajfault / d_dtlb_load_misses)
                   if d_dtlb_load_misses > 0 else None)
        dtlb_miss_rate = ((d_dtlb_load_misses / d_dtlb_loads)
                          if d_dtlb_loads > 0 else None)
        out.append(dict(
            t_rel_s=round(cur["t"] - t0, 3),
            interval_s=round(dt, 3),
            d_pgfault=d_pgfault,
            d_pgmajfault=d_pgmajfault,
            d_pgminfault=d_pgminfault,
            d_refault=d_refault,
            d_activate=d("cg_workingset_activate_file"),
            d_restore=d("cg_workingset_restore_file"),
            d_read_bytes=d("read_bytes"),
            d_minflt=d("minflt"),
            d_majflt=d("majflt"),
            d_seg_access=int(d_seg_access),
            d_received_nq=int(d_nq),
            d_dTLB_loads=d_dtlb_loads,
            d_dTLB_load_misses=d_dtlb_load_misses,
            interval_disk_fault_ratio=disk_fault_ratio,
            interval_classic_miss_ratio=classic,
            interval_dTLB_miss_rate=dtlb_miss_rate,
            pgfault_per_s=d_pgfault / dt,
            pgmajfault_per_s=d_pgmajfault / dt,
            pgminfault_per_s=d_pgminfault / dt,
            refault_per_s=d_refault / dt,
            read_bytes_per_s=d("read_bytes") / dt,
            seg_access_per_s=d_seg_access / dt,
            seg_access_per_nq=(d_seg_access / d_nq) if d_nq > 0 else None,
            dTLB_loads_per_s=d_dtlb_loads / dt,
            dTLB_load_misses_per_s=d_dtlb_load_misses / dt,
        ))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", nargs="+", default=VARIANTS,
                    help="variants to probe; default is all three compact ones")
    ap.add_argument("--warmup", type=float, default=60.0,
                    help="warmup seconds per run (page-cache fill)")
    ap.add_argument("--measure", type=float, default=120.0,
                    help="measurement seconds per run")
    ap.add_argument("--ef", type=int, default=200)
    ap.add_argument("--top-k", type=int, default=100)
    ap.add_argument("--sample-period", type=float, default=1.0,
                    help="counter sampling period (s)")
    ap.add_argument("--uri", default="http://localhost:19530")
    args = ap.parse_args()

    pid = find_milvus_pid()
    cg = cgroup_path(pid)
    print(f"Milvus pid={pid}  cgroup={cg}")

    client = MilvusClient(uri=args.uri)
    out_path = OUT / "cache_miss_probe.json"
    summary_path = OUT / "summary.txt"

    all_results = {}

    def flush(status):
        payload = {
            "args": vars(args),
            "milvus_pid": pid,
            "cgroup": cg,
            "status": status,
            "results": all_results,
        }
        tmp = out_path.with_suffix(".json.tmp")
        with tmp.open("w") as f:
            json.dump(payload, f, indent=2, default=str)
        os.replace(tmp, out_path)
        # Mirror the human-readable per-run blocks plus headline so
        # progress is preserved if the script is interrupted.
        tmp_txt = summary_path.with_suffix(".txt.tmp")
        tmp_txt.write_text("\n".join(_SUMMARY_LINES) + "\n")
        os.replace(tmp_txt, summary_path)

    flush("starting")
    print(f"  output: {out_path}")
    print(f"  summary: {summary_path}")
    snapshot_numa_and_cgroup(pid, cg, label="startup")
    flush("after_numa_startup")

    for variant in args.variants:
        try:
            q = load_queries(variant)
        except Exception as e:
            print(f"  load_queries({variant}) failed: {e}")
            continue
        ensure_only_loaded(client, variant)
        snapshot_numa_and_cgroup(pid, cg, label=f"after_load_{variant}")
        flush(f"after_load_{variant}")
        all_results.setdefault(variant, {})
        for run_tag, run_def in RUNS:
            summary, rows, interval_rows = run_one(
                client, variant, run_tag, run_def, q, args, pid, cg)
            all_results[variant][run_tag] = summary
            write_csv(
                OUT / f"cache_miss_probe_{variant}_{run_tag}.csv", rows)
            write_interval_csv(
                OUT / f"cache_miss_probe_{variant}_{run_tag}_interval.csv",
                interval_rows)
            flush(f"after_{variant}_{run_tag}")

            s = summary
            qps_str = " ".join(
                f"{label}={s['qps_per_tenant'][label]:.2f}"
                for label in s['qps_per_tenant'])
            tee(f"  -> qps_total={s['qps_total']:.2f} ({qps_str})")
            dfr = s['avg_disk_fault_ratio']
            cmr = s['avg_classic_miss_ratio']
            dfr_str = f"{dfr:.4g}" if dfr is not None else "n/a"
            cmr_str = f"{cmr:.4g}" if cmr is not None else "n/a"
            tee(f"     total_pgfault={s['total_pgfault']:,} "
                f"(maj={s['total_pgmajfault']:,} "
                f"min={s['total_minfault']:,})")
            tee(f"     pgmajfault/q={s['pgmajfault_per_query']:.1f} "
                f"avg_disk_fault_ratio={dfr_str} "
                f"avg_classic_miss_ratio={cmr_str}")
            dtlb_l = s.get('total_dTLB_loads') or 0
            dtlb_m = s.get('total_dTLB_load_misses') or 0
            dtlb_mr = s.get('dTLB_miss_rate')
            dtlb_mr_str = f"{dtlb_mr:.4g}" if dtlb_mr is not None else "n/a"
            tee(f"     dTLB_loads={dtlb_l:,} "
                f"dTLB_load_misses={dtlb_m:,} "
                f"dTLB_miss_rate={dtlb_mr_str}")
            tee(f"     total_refault={s['total_refault']:,} "
                f"refault/s={s['refault_per_s']:.1f} "
                f"refault/q={s['refault_per_query']:.2f}")
            tee(f"     read_bytes/q={s['read_bytes_per_query']/1e6:.2f}MB "
                f"read_bytes/s={s['read_bytes_per_s']/1e6:.2f}MB")
            seg_q = s['seg_access_per_query']
            tee(f"     mv_seg_access_total={s['mv_segment_access_total_delta']:,} "
                f"mv_received_nq={s['mv_received_nq_delta']:,} "
                f"seg_access/q={(seg_q if seg_q is not None else float('nan')):.2f} "
                f"seg_access/s={(s['seg_access_per_s'] or 0):.1f}")

    flush("done")
    # Final headline table: one row per (variant, run_tag).
    tee("\n=== headline ===")
    tee(f"{'variant':<18} {'run':<5} {'qps':>7} {'majflt/q':>10} "
        f"{'pgflt_tot':>13} {'majflt_tot':>13} "
        f"{'disk_fault_r':>13} {'classic_miss_r':>15} "
        f"{'refault_tot':>13} {'refault/s':>11} {'read_MB/q':>10} "
        f"{'seg_acc_tot':>13} {'seg_acc/q':>10}")
    for v, runs in all_results.items():
        for run_tag, s in runs.items():
            dfr = s['avg_disk_fault_ratio']
            cmr = s['avg_classic_miss_ratio']
            dfr_str = f"{dfr:.4g}" if dfr is not None else "n/a"
            cmr_str = f"{cmr:.4g}" if cmr is not None else "n/a"
            seg_q = s.get('seg_access_per_query')
            seg_q_str = f"{seg_q:.2f}" if seg_q is not None else "n/a"
            tee(f"{v:<18} {run_tag:<5} {s['qps_total']:>7.2f} "
                f"{(s['pgmajfault_per_query'] or 0):>10.1f} "
                f"{int(s['total_pgfault'] or 0):>13,} "
                f"{int(s['total_pgmajfault'] or 0):>13,} "
                f"{dfr_str:>13} {cmr_str:>15} "
                f"{int(s['total_refault'] or 0):>13,} "
                f"{(s['refault_per_s'] or 0):>11.1f} "
                f"{((s['read_bytes_per_query'] or 0)/1e6):>10.2f} "
                f"{int(s.get('mv_segment_access_total_delta') or 0):>13,} "
                f"{seg_q_str:>10}")
    flush("done")


if __name__ == "__main__":
    main()
