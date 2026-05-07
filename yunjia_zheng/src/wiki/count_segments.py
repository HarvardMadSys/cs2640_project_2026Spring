#!/usr/bin/env python3
"""Load each wiki collection in turn and report exact segment counts per partition.

Process for each collection:
  1. Release every other loaded collection so the target is the only one
     resident in QueryNode.
  2. Load the target if it is not already Loaded.
  3. Wait until get_load_state reports Loaded.
  4. Call utility.get_query_segment_info to get per-segment metadata
     (segment id, partition id, row count, state).
  5. Aggregate by partition id, then map partition id back to partition
     name by matching against partition stats row counts.
  6. Print a per-collection block plus accumulate to a CSV.
  7. Release the target unless --keep-loaded is set.

Output:
  - Pretty-printed block per collection on stdout
  - CSV summary at <HERE>/segment_counts.csv (or --out-csv)

Usage:
  python count_segments.py
  python count_segments.py --collections wiki_QC02_SS02 wiki_QC08_SS08
  python count_segments.py --keep-loaded                   # keep last loaded
  python count_segments.py --out-csv /tmp/segs.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import defaultdict
from pathlib import Path

from pymilvus import MilvusClient, connections, utility


HERE = Path(__file__).resolve().parent

DEFAULT_COLLECTIONS = [
    "wiki_QC02_SS02",
    "wiki_QC02_SS02_nocompact",
    "wiki_QC02_SS08",
    "wiki_QC02_SS08_nocompact",
    "wiki_QC08_SS08",
    "wiki_QC08_SS08_nocompact",
]


def wait_loaded(client: MilvusClient, col: str, timeout: float = 1200.0) -> bool:
    t0 = time.time()
    last_progress = -1
    while time.time() - t0 < timeout:
        st = client.get_load_state(col)
        state = str(st.get("state", st))
        progress = st.get("progress", None)
        if "Loaded" in state and "NotLoad" not in state:
            return True
        if progress is not None and progress != last_progress:
            print(f"  loading ... {progress}%", flush=True)
            last_progress = progress
        time.sleep(2)
    return False


def release_others(client: MilvusClient, target: str, log) -> None:
    for col in client.list_collections():
        if col == target:
            continue
        try:
            st = client.get_load_state(col)
            if "Loaded" in str(st) and "NotLoad" not in str(st):
                log(f"  releasing other loaded collection: {col}")
                client.release_collection(col)
        except Exception as e:
            log(f"  release({col}) skipped: {e}")


def count_segments(client: MilvusClient, col: str) -> list[dict]:
    """Return one row per partition with segment count and rows.

    Maps partitionID -> partition name by matching aggregate row counts
    from get_query_segment_info against partition_stats.
    """
    part_names = client.list_partitions(col)
    rows_by_name: dict[str, int] = {}
    for name in part_names:
        try:
            st = client.get_partition_stats(col, name)
            rows_by_name[name] = int(st.get("row_count", st.get("num_rows", 0)))
        except Exception:
            rows_by_name[name] = 0

    segs = utility.get_query_segment_info(col)
    seg_count_by_pid: dict[int, int] = defaultdict(int)
    rows_by_pid: dict[int, int] = defaultdict(int)
    states_by_pid: dict[int, set[str]] = defaultdict(set)
    for s in segs:
        seg_count_by_pid[s.partitionID] += 1
        rows_by_pid[s.partitionID] += int(s.num_rows)
        try:
            states_by_pid[s.partitionID].add(str(s.state))
        except Exception:
            pass

    # Greedy matching: assign each partition_name to the still-unused
    # partition_id with the closest row count. Each tenant's row count is
    # distinct enough that this is unambiguous in practice.
    used: set[int] = set()
    out: list[dict] = []
    for name in part_names:
        target_rows = rows_by_name[name]
        if target_rows == 0:
            out.append({
                "partition_name": name,
                "partition_id": None,
                "segments": 0,
                "rows_via_segments": 0,
                "rows_via_stats": 0,
                "states": "",
            })
            continue
        best_pid, best_diff = None, float("inf")
        for pid, n in rows_by_pid.items():
            if pid in used:
                continue
            d = abs(n - target_rows)
            if d < best_diff:
                best_diff, best_pid = d, pid
        if best_pid is not None:
            used.add(best_pid)
            out.append({
                "partition_name": name,
                "partition_id": int(best_pid),
                "segments": seg_count_by_pid[best_pid],
                "rows_via_segments": rows_by_pid[best_pid],
                "rows_via_stats": target_rows,
                "states": ",".join(sorted(states_by_pid[best_pid])),
            })
        else:
            out.append({
                "partition_name": name,
                "partition_id": None,
                "segments": 0,
                "rows_via_segments": 0,
                "rows_via_stats": target_rows,
                "states": "",
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--collections", nargs="+", default=DEFAULT_COLLECTIONS)
    ap.add_argument("--uri", default="http://localhost:19530")
    ap.add_argument("--out-csv", default=str(HERE / "segment_counts.csv"))
    ap.add_argument("--keep-loaded", action="store_true",
                    help="Do not release each collection after counting. "
                         "Default is to release so the next collection has "
                         "the cgroup's RAM to itself.")
    ap.add_argument("--load-timeout", type=float, default=1200.0,
                    help="Seconds to wait for Loaded per collection.")
    args = ap.parse_args()

    client = MilvusClient(uri=args.uri)
    connections.connect(uri=args.uri)

    existing = set(client.list_collections())
    requested = list(args.collections)
    missing = [c for c in requested if c not in existing]
    if missing:
        print(f"WARNING: missing collections, skipping: {missing}")

    all_rows: list[dict] = []
    summary: list[tuple[str, int, int, list[dict]]] = []  # (col, total_segs, total_rows, rows)

    for col in requested:
        if col not in existing:
            continue
        print(f"\n=== {col} ===", flush=True)
        release_others(client, col, log=print)

        already = "Loaded" in str(client.get_load_state(col))
        if not already:
            print(f"  loading {col} ...", flush=True)
            t0 = time.time()
            client.load_collection(col)
            if not wait_loaded(client, col, timeout=args.load_timeout):
                print(f"  ERROR: not Loaded after {args.load_timeout}s; "
                      f"skipping this collection")
                continue
            print(f"  loaded in {time.time() - t0:.1f}s", flush=True)
        else:
            print(f"  already loaded", flush=True)

        rows = count_segments(client, col)
        total_segs = sum(r["segments"] for r in rows)
        total_rows_stats = sum(r["rows_via_stats"] for r in rows)
        total_rows_segs = sum(r["rows_via_segments"] for r in rows)
        print(f"  --- {col} ---  total_segments={total_segs}  "
              f"total_rows={total_rows_stats:,}  "
              f"(via_segments={total_rows_segs:,})", flush=True)
        print(f"  {'partition':<25} {'segments':>10} {'rows(stats)':>14} "
              f"{'rows(segs)':>14}  {'diff':>8}  pid", flush=True)
        for r in rows:
            diff = r["rows_via_segments"] - r["rows_via_stats"]
            warn = "" if abs(diff) <= max(1, r["rows_via_stats"] // 1000) else "  <-- mismatch"
            print(f"  {r['partition_name']:<25} {r['segments']:>10} "
                  f"{r['rows_via_stats']:>14,} {r['rows_via_segments']:>14,} "
                  f"{diff:>8,}  {r['partition_id']}{warn}", flush=True)

        for r in rows:
            r_with = dict(r)
            r_with["collection"] = col
            r_with["total_segments"] = total_segs
            r_with["total_rows"] = total_rows_stats
            all_rows.append(r_with)
        summary.append((col, total_segs, total_rows_stats, rows))

        if not args.keep_loaded:
            try:
                client.release_collection(col)
            except Exception as e:
                print(f"  release failed: {e}")

    # CSV out
    if all_rows:
        out = Path(args.out_csv)
        keys = ["collection", "partition_name", "partition_id", "segments",
                "rows_via_segments", "rows_via_stats", "states",
                "total_segments", "total_rows"]
        with out.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in all_rows:
                w.writerow({k: r.get(k, "") for k in keys})
        print(f"\nWrote {out}")

    # Final compact summary table, includes EVERY partition the script saw
    # (including _default, even when it is empty).
    print("\n=== summary (segments per partition) ===")
    if summary:
        all_names: list[str] = []
        seen: set[str] = set()
        for _, _, _, rs in summary:
            for r in rs:
                if r["partition_name"] not in seen:
                    seen.add(r["partition_name"])
                    all_names.append(r["partition_name"])
        # Stable: _default first, then the rest in insertion order
        all_names.sort(key=lambda n: (0 if n == "_default" else 1, n))

        header = f"{'collection':<32}  total " + "  ".join(f"{n:>17}" for n in all_names)
        print(header)
        for col, total_segs, _, rows in summary:
            by_name = {r["partition_name"]: r["segments"] for r in rows}
            cells = "  ".join(f"{by_name.get(n, 0):>17}" for n in all_names)
            print(f"{col:<32}  {total_segs:>5}  {cells}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
