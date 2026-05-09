"""Compute the reuse-distance distribution for an oracleGeneral .zst trace.

Reuse distance for the request at vtime i = next_access_vtime - i (in requests).
A sentinel next_access_vtime (no future access) is treated as +inf and excluded
from the percentile statistics.
"""
import struct
import sys
from pathlib import Path
from collections import Counter

import zstandard as zstd

ORACLE_GENERAL_STRUCT = struct.Struct("<IQIq")
ORACLE_GENERAL_SIZE = ORACLE_GENERAL_STRUCT.size  # 24 bytes


def reuse_distance_distribution(path: Path, limit: int | None = None,
                                W_values: list[int] | None = None):
    """Stream the trace and bucket reuse distances by power-of-two ranges.
    Returns (bucket_counts, total_reuses, total_records, n_no_reuse,
             frac_under_W: dict[int, float]).
    """
    if W_values is None:
        W_values = [100, 1_000, 10_000]

    bucket_counts: Counter[int] = Counter()
    n_records = 0
    n_reuses = 0
    n_no_reuse = 0
    under_W: dict[int, int] = {W: 0 for W in W_values}

    SENTINEL_THRESHOLD = 1 << 62  # libCacheSim uses INT64_MAX-ish

    with open(path, "rb") as f:
        if str(path).endswith(".zst"):
            dctx = zstd.ZstdDecompressor()
            stream = dctx.stream_reader(f)
        else:
            stream = f  # plain .bin
        i = 0
        while True:
            chunk = stream.read(ORACLE_GENERAL_SIZE * 16384)
            if not chunk:
                break
            usable = (len(chunk) // ORACLE_GENERAL_SIZE) * ORACLE_GENERAL_SIZE
            for off in range(0, usable, ORACLE_GENERAL_SIZE):
                _ts, _oid, _sz, nxt = ORACLE_GENERAL_STRUCT.unpack_from(chunk, off)
                vtime = i  # 0-indexed
                i += 1
                n_records += 1
                if nxt < 0 or nxt >= SENTINEL_THRESHOLD or nxt <= vtime:
                    n_no_reuse += 1
                else:
                    rd = nxt - vtime
                    n_reuses += 1
                    # Power-of-two bucket: bucket = floor(log2(rd))
                    bucket = rd.bit_length() - 1
                    bucket_counts[bucket] += 1
                    for W in W_values:
                        if rd < W:
                            under_W[W] += 1
                if limit is not None and n_records >= limit:
                    break
            if limit is not None and n_records >= limit:
                break

    frac_under_W = {W: (under_W[W] / max(n_reuses, 1)) for W in W_values}
    return bucket_counts, n_reuses, n_records, n_no_reuse, frac_under_W


def print_report(path: Path, limit: int | None = None):
    print(f"Trace: {path}")
    print(f"Limit: {limit}")
    bc, n_reuses, n_records, n_no_reuse, frac = reuse_distance_distribution(
        path, limit=limit, W_values=[100, 200, 500, 1_000, 2_000, 5_000, 10_000, 100_000])
    print(f"\nRecords:               {n_records:,}")
    print(f"With future reuse:     {n_reuses:,} ({n_reuses/max(n_records,1):.1%})")
    print(f"No future reuse:       {n_no_reuse:,} ({n_no_reuse/max(n_records,1):.1%})")
    print()
    print("Reuse-distance distribution (power-of-two buckets, in # requests):")
    print(f"  {'bucket':<12} {'range':<24} {'count':>14} {'cum %':>8}")
    cum = 0
    total = max(n_reuses, 1)
    for bucket in sorted(bc.keys()):
        lo, hi = 1 << bucket, (1 << (bucket + 1)) - 1
        cum += bc[bucket]
        print(f"  2^{bucket:<10} [{lo:>10}, {hi:>10}] {bc[bucket]:>14,} {cum/total:>7.1%}")
    print()
    print("Fraction of reuses with distance < W:")
    for W, f in frac.items():
        print(f"  W = {W:>7}:    {f:.1%}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: reuse_distance.py <trace.oracleGeneral.zst> [limit]")
        sys.exit(1)
    p = Path(sys.argv[1])
    lim = int(sys.argv[2]) if len(sys.argv) > 2 else None
    print_report(p, lim)
