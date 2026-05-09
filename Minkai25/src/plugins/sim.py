"""
Self-contained Python simulator harness for the CS 264 caching project.

Why this exists: the libcachesim Python wheel fails to build in this environment.
Rather than block on the build, this module provides a minimal drop-in:
  - Request struct mirroring libcachesim.Request (obj_id, obj_size, next_access_vtime)
  - Cache base class with the same five hooks (init, hit, miss, evict, remove)
  - process_trace() returning (req_miss_ratio, byte_miss_ratio)

Trace format: oracleGeneral CSV "time, object, size, next_access_vtime"
(exactly the layout of data/twitter_cluster52.csv).

The next_access_vtime field is preserved on every Request because the headline
attack idea #1 (learned S→M promotion gate) uses it as a Belady label.

Defaults follow ATTACK_PLAN.md: ignore_obj_size=True, since the Twitter slab
allocator means byte-MR realism is not the point and prior work uses request-MR.
"""

from __future__ import annotations

import csv
import gzip
import io
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TWITTER_CSV = DATA_DIR / "twitter_cluster52.csv"
TWITTER_ZST = DATA_DIR / "twitter_cluster52_10m.csv.zst"
CLOUDPHYSICS_CSV = DATA_DIR / "cloudPhysicsIO.csv"
CLUSTER10_BIN = DATA_DIR / "cluster10.oracleGeneral.sample10.zst"
CLUSTER26_BIN = DATA_DIR / "cluster26.oracleGeneral.sample10.zst"
CLUSTER45_BIN = DATA_DIR / "cluster45.oracleGeneral.sample10.zst"
CLUSTER50_BIN = DATA_DIR / "cluster50.oracleGeneral.sample10.zst"
MSR_HM_0_BIN = DATA_DIR / "msr_hm_0.oracleGeneral.zst"
MSR_PROJ_0_BIN = DATA_DIR / "msr_proj_0.oracleGeneral.zst"
MSR_PRXY_0_BIN = DATA_DIR / "msr_prxy_0.oracleGeneral.zst"
ALIBABA_110_BIN = DATA_DIR / "alibaba_alibabaBlock_110.oracleGeneral.zst"
ALIBABA_185_BIN = DATA_DIR / "alibaba_alibabaBlock_185.oracleGeneral.zst"
W105_BIN = DATA_DIR / "w105.oracleGeneral.bin.zst"
WIKI_2019T_BIN = DATA_DIR / "wiki_2019t.oracleGeneral.zst"
META_REAG_BIN = DATA_DIR / "meta_reag.oracleGeneral.zst"
BLOCK1_BIN = DATA_DIR / "block_traces_1.oracleGeneral.bin.zst"

# oracleGeneral binary record: {uint32 ts, uint64 obj_id, uint32 sz, int64 nxt}
# 24 bytes/record, little-endian.
import struct
ORACLE_GENERAL_STRUCT = struct.Struct("<IQIq")
ORACLE_GENERAL_SIZE = ORACLE_GENERAL_STRUCT.size  # 24


@dataclass(slots=True)
class Request:
    obj_id: int
    obj_size: int
    next_access_vtime: int = -1
    timestamp: int = 0


def read_twitter_csv(path: Path = TWITTER_CSV, limit: Optional[int] = None,
                     ignore_obj_size: bool = True) -> Iterator[Request]:
    """Yield Request objects from a CSV in oracleGeneral schema."""
    open_fn = open
    mode = "rt"
    if str(path).endswith(".zst"):
        # Decode .zst lazily via zstandard if available; otherwise expect plain csv.
        try:
            import zstandard as zstd  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                f"Cannot read {path}: zstandard not installed. Use the plain .csv."
            ) from e
        f = open(path, "rb")
        dctx = zstd.ZstdDecompressor()
        stream = dctx.stream_reader(f)
        text = io.TextIOWrapper(stream, encoding="utf-8")
        reader = csv.reader(text)
    else:
        f = open(path, mode)
        reader = csv.reader(f)

    try:
        n = 0
        for row in reader:
            if not row or row[0].lstrip().startswith("#"):
                continue
            try:
                t, oid, sz, nxt = row
            except ValueError:
                continue
            req = Request(
                obj_id=int(oid),
                obj_size=1 if ignore_obj_size else max(1, int(sz)),
                next_access_vtime=int(nxt),
                timestamp=int(t),
            )
            yield req
            n += 1
            if limit is not None and n >= limit:
                break
    finally:
        f.close()


class Cache:
    """Base class. Subclass and override hit/miss/evict/remove. process_trace()
    handles the simulation loop and miss-ratio bookkeeping."""

    def __init__(self, cache_size: int):
        self.cache_size = cache_size
        # subclasses are expected to maintain their own occupancy bookkeeping;
        # this set is the canonical "is this object currently in the cache?"
        # used by process_trace to decide hit vs miss without polling subclass.
        self._members: set[int] = set()
        self._used_bytes: int = 0
        self._sizes: dict[int, int] = {}

    # ── hooks (override) ────────────────────────────────────────────────────

    def on_hit(self, req: Request) -> None:
        pass

    def on_miss(self, req: Request) -> None:
        """Called BEFORE eviction. Insert state for the new object here, but do
        not yet mark `_members` — process_trace handles membership and eviction."""
        pass

    def on_evict(self, req: Request) -> int:
        """Return obj_id of the victim. Subclass MUST remove its bookkeeping
        for that obj_id before returning."""
        raise NotImplementedError

    def on_remove(self, obj_id: int) -> None:
        pass

    # ── helpers ─────────────────────────────────────────────────────────────

    def __contains__(self, obj_id: int) -> bool:
        return obj_id in self._members


def process_trace(cache: Cache, trace: Iterable[Request]) -> tuple[float, float]:
    """Run a trace through a Cache. Returns (req_miss_ratio, byte_miss_ratio)."""
    n_req = 0
    n_miss = 0
    bytes_req = 0
    bytes_miss = 0
    for req in trace:
        n_req += 1
        bytes_req += req.obj_size
        if req.obj_id in cache._members:
            cache.on_hit(req)
            continue
        # Miss path
        n_miss += 1
        bytes_miss += req.obj_size

        cache.on_miss(req)
        # Always admit (we treat admission decisions as algorithm-internal: an
        # algorithm that "rejects" still calls on_miss but never adds to
        # _members; on_evict is a no-op when no admission occurred).
        if req.obj_id in cache._members:
            # algorithm explicitly admitted (and updated _members). Evict if over.
            while cache._used_bytes > cache.cache_size and len(cache._members) > 0:
                victim = cache.on_evict(req)
                if victim is None:
                    break
                _remove_member(cache, victim)
    return (n_miss / max(n_req, 1), bytes_miss / max(bytes_req, 1))


def _add_member(cache: Cache, obj_id: int, size: int) -> None:
    """Subclasses call this from on_miss when they admit an object."""
    if obj_id in cache._members:
        return
    cache._members.add(obj_id)
    cache._sizes[obj_id] = size
    cache._used_bytes += size


def _remove_member(cache: Cache, obj_id: int) -> None:
    if obj_id not in cache._members:
        return
    cache._members.discard(obj_id)
    sz = cache._sizes.pop(obj_id, 0)
    cache._used_bytes -= sz
    cache.on_remove(obj_id)


# Public helpers for plugin authors -------------------------------------------

def admit(cache: Cache, req: Request) -> None:
    """Admit a request to the cache. Call from on_miss when you want this
    object inserted. The harness will trigger evictions if cache is over size."""
    _add_member(cache, req.obj_id, req.obj_size)


def discard(cache: Cache, obj_id: int) -> None:
    """Force-remove an object (used by algorithms that need it during evict
    walks). NB: on_remove is fired."""
    _remove_member(cache, obj_id)


def fill_next_access_vtime(reqs: list[Request], sentinel: int = 10**18) -> list[Request]:
    """Backward pass to populate next_access_vtime for traces that don't ship
    with it (CloudPhysics, Zipf synthetic). Each request's next_access_vtime
    is set to the trace index of the next reference to the same obj_id, or
    `sentinel` if there is no future reference. Modifies in place; returns
    the list for chaining."""
    last_seen: dict[int, int] = {}
    # Walk backwards: for each idx, the next-access of reqs[idx].obj_id
    # is whatever last_seen says, then update last_seen with idx.
    for i in range(len(reqs) - 1, -1, -1):
        oid = reqs[i].obj_id
        reqs[i].next_access_vtime = last_seen.get(oid, sentinel)
        last_seen[oid] = i
    return reqs


def read_cloudphysics_csv(path: Path = CLOUDPHYSICS_CSV,
                          limit: Optional[int] = None,
                          ignore_obj_size: bool = True) -> list[Request]:
    """CloudPhysics block-I/O CSV: 'version,time,op,size,lbn'. Treats the
    logical block number as obj_id. Returns a fully-materialized list with
    next_access_vtime populated by a backward pass.

    NB: this is a block I/O trace, very different access pattern from KV.
    Use for OOD / scan-resistance sanity checks (per ATTACK_PLAN.md §1)."""
    reqs: list[Request] = []
    with open(path, "rt") as f:
        reader = csv.reader(f)
        header_consumed = False
        for row in reader:
            if not header_consumed:
                header_consumed = True
                # First row is the header in CloudPhysics CSVs.
                if row and row[0] == "version":
                    continue
            if not row or len(row) < 5:
                continue
            try:
                _ver, t, _op, sz, lbn = row[:5]
                req = Request(
                    obj_id=int(lbn),
                    obj_size=1 if ignore_obj_size else max(1, int(sz)),
                    next_access_vtime=-1,  # filled below
                    timestamp=int(t),
                )
            except ValueError:
                continue
            reqs.append(req)
            if limit is not None and len(reqs) >= limit:
                break
    fill_next_access_vtime(reqs)
    return reqs


def read_twitter_zst(path: Path = TWITTER_ZST, limit: Optional[int] = None,
                     ignore_obj_size: bool = True) -> list[Request]:
    """Read the 10M zstd-compressed Twitter slice. Requires the `zstandard`
    Python package; if unavailable, raises with a clear install hint."""
    try:
        import zstandard as zstd  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "Reading .zst traces requires `zstandard`: "
            "python3.11 -m pip install --user zstandard"
        ) from e
    reqs: list[Request] = []
    with open(path, "rb") as f:
        dctx = zstd.ZstdDecompressor()
        stream = dctx.stream_reader(f)
        text = io.TextIOWrapper(stream, encoding="utf-8")
        reader = csv.reader(text)
        for row in reader:
            if not row or row[0].lstrip().startswith("#"):
                continue
            try:
                t, oid, sz, nxt = row
            except ValueError:
                continue
            reqs.append(Request(
                obj_id=int(oid),
                obj_size=1 if ignore_obj_size else max(1, int(sz)),
                next_access_vtime=int(nxt),
                timestamp=int(t),
            ))
            if limit is not None and len(reqs) >= limit:
                break
    return reqs


def synth_zipf(n: int = 200_000, n_unique: int = 20_000, alpha: float = 1.0,
               seed: int = 0) -> list[Request]:
    """Programmatic Zipf trace — useful as an "always available" sanity check
    that doesn't depend on the data/ directory.

    `alpha` is the Zipf exponent. Twitter cluster52 is super-Zipfian with
    α > 1; alpha=1.0 is borderline; alpha=0.7 is much flatter (closer to
    block I/O). Vary alpha to stress-test promotion-gate behaviour without
    a real trace."""
    import random
    rng = random.Random(seed)
    # Inverse-CDF sampling for Zipf with bounded support [1, n_unique].
    # We use rejection: draw k ~ 1 + floor(exp(rng()) ** (1/alpha)) is too
    # tail-heavy; simpler is the truncated-Zipf via cumulative weights.
    weights = [1.0 / (k ** alpha) for k in range(1, n_unique + 1)]
    total = sum(weights)
    cumulative = []
    s = 0.0
    for w in weights:
        s += w
        cumulative.append(s / total)
    # Generate.
    reqs: list[Request] = []
    for i in range(n):
        u = rng.random()
        # Binary search.
        lo, hi = 0, n_unique - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if cumulative[mid] >= u:
                hi = mid
            else:
                lo = mid + 1
        oid = lo + 1
        reqs.append(Request(obj_id=oid, obj_size=1, next_access_vtime=-1, timestamp=i))
    fill_next_access_vtime(reqs)
    return reqs


def read_oracle_general_zst(path: Path, limit: Optional[int] = None,
                            ignore_obj_size: bool = True) -> list[Request]:
    """Read libCacheSim's oracleGeneral binary format from a .zst file.
    Format: {uint32 ts, uint64 obj_id, uint32 sz, int64 next_access_vtime},
    24 bytes/record, little-endian. next_access_vtime is provided by the
    trace, so no backward pass needed (sentinel = INT64_MAX or similar)."""
    try:
        import zstandard as zstd  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "Reading .zst traces requires `zstandard`: "
            "python3.11 -m pip install --user zstandard"
        ) from e
    reqs: list[Request] = []
    with open(path, "rb") as f:
        dctx = zstd.ZstdDecompressor()
        stream = dctx.stream_reader(f)
        while True:
            chunk = stream.read(ORACLE_GENERAL_SIZE * 4096)
            if not chunk:
                break
            # Truncate to whole records.
            usable = (len(chunk) // ORACLE_GENERAL_SIZE) * ORACLE_GENERAL_SIZE
            for off in range(0, usable, ORACLE_GENERAL_SIZE):
                ts, oid, sz, nxt = ORACLE_GENERAL_STRUCT.unpack_from(chunk, off)
                reqs.append(Request(
                    obj_id=oid,
                    obj_size=1 if ignore_obj_size else max(1, sz),
                    next_access_vtime=nxt,
                    timestamp=ts,
                ))
                if limit is not None and len(reqs) >= limit:
                    return reqs
    return reqs


# Registry: name → (loader, default kwargs). Used by run_experiments.py.
TRACES: dict[str, callable] = {
    "twitter": lambda limit: list(read_twitter_csv(TWITTER_CSV, limit=limit)),
    "twitter_zst": lambda limit: read_twitter_zst(TWITTER_ZST, limit=limit),
    "cloudphysics": lambda limit: read_cloudphysics_csv(CLOUDPHYSICS_CSV, limit=limit),
    "cluster10": lambda limit: read_oracle_general_zst(CLUSTER10_BIN, limit=limit),
    "cluster26": lambda limit: read_oracle_general_zst(CLUSTER26_BIN, limit=limit),
    "cluster45": lambda limit: read_oracle_general_zst(CLUSTER45_BIN, limit=limit),
    "cluster50": lambda limit: read_oracle_general_zst(CLUSTER50_BIN, limit=limit),
    "msr_hm_0":  lambda limit: read_oracle_general_zst(MSR_HM_0_BIN, limit=limit),
    "msr_proj_0": lambda limit: read_oracle_general_zst(MSR_PROJ_0_BIN, limit=limit),
    "msr_prxy_0": lambda limit: read_oracle_general_zst(MSR_PRXY_0_BIN, limit=limit),
    "alibaba_110": lambda limit: read_oracle_general_zst(ALIBABA_110_BIN, limit=limit),
    "alibaba_185": lambda limit: read_oracle_general_zst(ALIBABA_185_BIN, limit=limit),
    "w105":      lambda limit: read_oracle_general_zst(W105_BIN, limit=limit),
    "wiki":      lambda limit: read_oracle_general_zst(WIKI_2019T_BIN, limit=limit),
    "meta_reag": lambda limit: read_oracle_general_zst(META_REAG_BIN, limit=limit),
    "block1":    lambda limit: read_oracle_general_zst(BLOCK1_BIN, limit=limit),
    "zipf_heavy": lambda limit: synth_zipf(n=limit or 200_000, n_unique=20_000,
                                           alpha=1.2),
    "zipf_flat": lambda limit: synth_zipf(n=limit or 200_000, n_unique=20_000,
                                          alpha=0.7),
}


def quick_summary(name: str, miss_ratios: dict[int, tuple[float, float]]) -> str:
    lines = [f"=== {name} ==="]
    lines.append(f"{'cache_size':>12}  {'req_MR':>8}  {'byte_MR':>8}")
    for sz, (req_mr, byte_mr) in sorted(miss_ratios.items()):
        lines.append(f"{sz:>12}  {req_mr:>8.4f}  {byte_mr:>8.4f}")
    return "\n".join(lines)
