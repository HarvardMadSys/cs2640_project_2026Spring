"""Convert local CSV traces (twitter_cluster52, cloudPhysicsIO) to libCacheSim's
oracleGeneral binary format with properly populated `next_access_vtime`.

Format: 24 bytes per record, little-endian:
    uint32 timestamp; uint64 obj_id; uint32 size; int64 next_access_vtime
The vtime sentinel for "no future access" is INT64_MAX (== 9223372036854775807).
libCacheSim's Belady reads this field directly; downstream policies ignore it.

Existing cloudPhysicsIO.oracleGeneral.bin shipped with the repo is unusable for
Belady (all sentinel vtimes). This script regenerates it with the backward pass
that sim.fill_next_access_vtime does, but written to disk so cachesim can read it.

Usage:
    python3.11 plugins/convert_traces.py
"""

from __future__ import annotations

import csv
import struct
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
TWITTER_CSV = DATA / "twitter_cluster52.csv"
CLOUDPHYSICS_CSV = DATA / "cloudPhysicsIO.csv"
TWITTER_BIN = DATA / "twitter_cluster52.oracleGeneral.bin"
CLOUDPHYSICS_BIN = DATA / "cloudPhysicsIO.oracleGeneral.bin"

REC = struct.Struct("<IQIq")
SENTINEL = (1 << 63) - 1  # INT64_MAX


def _backward_pass(records: list[tuple[int, int, int]]) -> list[tuple[int, int, int, int]]:
    """records = [(ts, oid, sz)]; returns [(ts, oid, sz, next_idx)] with the
    next-access vtime per request."""
    next_idx = [SENTINEL] * len(records)
    last_seen: dict[int, int] = {}
    for i in range(len(records) - 1, -1, -1):
        oid = records[i][1]
        next_idx[i] = last_seen.get(oid, SENTINEL)
        last_seen[oid] = i
    return [(ts, oid, sz, n) for (ts, oid, sz), n in zip(records, next_idx)]


def convert_twitter(src: Path = TWITTER_CSV, dst: Path = TWITTER_BIN) -> int:
    """Twitter CSV already has `next_access_vtime`; just repack as binary."""
    n = 0
    with open(src, "rt") as f, open(dst, "wb") as out:
        reader = csv.reader(f)
        for row in reader:
            if not row or row[0].lstrip().startswith("#"):
                continue
            try:
                t, oid, sz, nxt = row
                t = int(t.strip()); oid = int(oid.strip())
                sz = int(sz.strip()); nxt = int(nxt.strip())
            except ValueError:
                continue
            # Twitter CSVs use -1 for "no future access"; cachesim wants INT64_MAX.
            if nxt < 0:
                nxt = SENTINEL
            out.write(REC.pack(t & 0xFFFFFFFF, oid, sz, nxt))
            n += 1
    return n


def convert_cloudphysics(src: Path = CLOUDPHYSICS_CSV,
                         dst: Path = CLOUDPHYSICS_BIN) -> int:
    """CloudPhysics CSV is `version,time,op,size,lbn` — no next-access. Compute
    via a backward pass over the materialized list."""
    records: list[tuple[int, int, int]] = []
    with open(src, "rt") as f:
        reader = csv.reader(f)
        first = True
        for row in reader:
            if first:
                first = False
                if row and row[0].strip() == "version":
                    continue
            if not row or len(row) < 5:
                continue
            try:
                _ver, t, _op, sz, lbn = row[:5]
                records.append((int(t), int(lbn), max(1, int(sz))))
            except ValueError:
                continue
    populated = _backward_pass(records)
    with open(dst, "wb") as out:
        for (t, oid, sz, nxt) in populated:
            out.write(REC.pack(t & 0xFFFFFFFF, oid, sz, nxt))
    return len(populated)


def main() -> None:
    print(f"converting {TWITTER_CSV} -> {TWITTER_BIN} ...", file=sys.stderr)
    n_t = convert_twitter()
    print(f"  {n_t:,} records", file=sys.stderr)

    print(f"converting {CLOUDPHYSICS_CSV} -> {CLOUDPHYSICS_BIN} ...", file=sys.stderr)
    n_c = convert_cloudphysics()
    print(f"  {n_c:,} records", file=sys.stderr)


if __name__ == "__main__":
    main()
