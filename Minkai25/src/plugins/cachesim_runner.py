"""Wrapper around libCacheSim's cachesim C++ binary.

Provides ~65× speedup vs our Python harness for the canonical baselines
(FIFO, LRU, LFU, SIEVE, ARC, S3FIFO, Belady, LeCaR, LIRS, Cacheus, ...).
The Python harness still owns the learned variants (V4 family, OptT/OptS/OptST
meta-aliases) where we need access to internal cache state.

Dispatch lives in run_experiments.py: any algo in CACHESIM_ALGOS for a trace
whose file is in TRACE_FILES gets batched into a single cachesim call per
(trace, cache_size) cell.

cachesim output format (one line per algo):
  "<trace_path> <ALGO_NAME> cache size <SZ>, <N> req, miss ratio <MR>"
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parent.parent
CACHESIM_BIN = ROOT / "_build" / "bin" / "cachesim"
DATA_DIR = ROOT / "data"


# Trace name → file path. Only file-backed binary (oracleGeneral) traces;
# cachesim doesn't read our synthetic Zipf. The two CSV traces (twitter,
# cloudphysics) are routed through `convert_traces.py` to a binary on disk
# so they participate in the cachesim path too.
TRACE_FILES: dict[str, Path] = {
    # Twitter Twemcache KV (sample10 except cluster52 which is 1M-row local CSV).
    "twitter":      DATA_DIR / "twitter_cluster52.oracleGeneral.bin",
    "cluster10":    DATA_DIR / "cluster10.oracleGeneral.sample10.zst",
    "cluster26":    DATA_DIR / "cluster26.oracleGeneral.sample10.zst",
    "cluster45":    DATA_DIR / "cluster45.oracleGeneral.sample10.zst",
    "cluster50":    DATA_DIR / "cluster50.oracleGeneral.sample10.zst",
    # MSR Cambridge block I/O.
    "msr_hm_0":     DATA_DIR / "msr_hm_0.oracleGeneral.zst",
    "msr_proj_0":   DATA_DIR / "msr_proj_0.oracleGeneral.zst",
    "msr_prxy_0":   DATA_DIR / "msr_prxy_0.oracleGeneral.zst",
    # Alibaba block.
    "alibaba_110":  DATA_DIR / "alibaba_alibabaBlock_110.oracleGeneral.zst",
    "alibaba_185":  DATA_DIR / "alibaba_alibabaBlock_185.oracleGeneral.zst",
    # CloudPhysics block I/O (regenerated from CSV via convert_traces.py).
    "cloudphysics": DATA_DIR / "cloudPhysicsIO.oracleGeneral.bin",
    "w105":         DATA_DIR / "w105.oracleGeneral.bin.zst",
    # CDN.
    "wiki":         DATA_DIR / "wiki_2019t.oracleGeneral.zst",
    "meta_reag":    DATA_DIR / "meta_reag.oracleGeneral.zst",
    # Meta storage block.
    "block1":       DATA_DIR / "block_traces_1.oracleGeneral.bin.zst",
}


# Map our friendly Python algo names → cachesim's canonical names.
# cachesim emits "S3FIFO-0.1000-2" etc. for parameterized algos; we match by
# uppercase prefix below.
#
# Excluded from this list (parser rejects them as "admission algo not supported"):
#   s3fifod, fifo-reinsertion, lfu_da, lfu_da_aging, ...
# These appear to need a different invocation flag; not worth chasing for now.
CACHESIM_ALGOS: dict[str, str] = {
    "FIFO":    "fifo",
    "LRU":     "lru",
    "LFU":     "lfu",
    "SIEVE":   "sieve",
    "ARC":     "arc",
    "TwoQ":    "twoq",
    "S3FIFO":  "s3fifo",        # cachesim default: S=0.10, T=2 (the paper default)
    "Belady":  "belady",
    "LeCaR":   "lecar",
    "LIRS":    "lirs",
    "Cacheus": "cacheus",
    "ClockPro": "clockpro",
    "Hyperbolic": "hyperbolic",
    "WTinyLFU": "wtinylfu",
    "GDSF":    "gdsf",
    "LHD":     "lhd",
    "QDLP":    "qdlp",
    "SLRU":    "slru",
}


_OUTPUT_LINE = re.compile(
    r"\s(?P<algo>\S+)\s+cache\s+size\s+(?P<sz>\d+),\s+(?P<n>\d+)\s+req,\s+miss\s+ratio\s+(?P<mr>[\d.]+)"
)


def is_cachesim_available() -> bool:
    return CACHESIM_BIN.exists() and CACHESIM_BIN.is_file()


def has_trace_file(trace_name: str) -> bool:
    return trace_name in TRACE_FILES and TRACE_FILES[trace_name].exists()


# cachesim segfaults at >16 algos in a single batch (likely a hardcoded array
# size in libCacheSim). Verified empirically: 16 algos OK, 17 segfaults.
MAX_BATCH = 16


def _run_one_batch(
    trace_name: str, algos: list[str], cache_size: int,
    ignore_size: bool, num_req: Optional[int],
) -> dict[str, tuple[float, float, float]]:
    """Single cachesim invocation. Matches output to input by ordinal position
    (cachesim emits one line per requested algo, in input order). The
    name-prefix matching we used previously broke for SLRU (cachesim renames
    it `S4LRU(25:25:25:25)`) — ordinal matching avoids that class of bug."""
    cs_names: list[str] = []
    for a in algos:
        if a not in CACHESIM_ALGOS:
            raise RuntimeError(f"algo '{a}' not in CACHESIM_ALGOS")
        cs_names.append(CACHESIM_ALGOS[a])

    cmd = [
        str(CACHESIM_BIN), str(TRACE_FILES[trace_name]), "oracleGeneral",
        ",".join(cs_names), str(cache_size),
    ]
    if ignore_size:
        cmd += ["--ignore-obj-size", "1"]
    if num_req is not None:
        cmd += ["--num-req", str(num_req)]

    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    elapsed = time.time() - t0
    if proc.returncode != 0:
        raise RuntimeError(f"cachesim exited {proc.returncode}: {proc.stderr[:400]}")

    # Collect (algo_label, mr) lines in emission order, then zip to algos.
    parsed: list[tuple[str, float]] = []
    for line in proc.stdout.splitlines():
        m = _OUTPUT_LINE.search(line)
        if not m:
            continue
        parsed.append((m.group("algo"), float(m.group("mr"))))

    if len(parsed) != len(algos):
        raise RuntimeError(
            f"cachesim emitted {len(parsed)} result lines but expected {len(algos)} "
            f"(algos={algos}). stdout:\n{proc.stdout[:800]}")

    per_algo_secs = elapsed / max(1, len(algos))
    out: dict[str, tuple[float, float, float]] = {}
    for our_name, (_label, mr) in zip(algos, parsed):
        out[our_name] = (mr, mr, per_algo_secs)
    return out


def run_cachesim_batch(
    trace_name: str,
    algos: list[str],
    cache_size: int,
    ignore_size: bool = True,
    num_req: Optional[int] = None,
) -> dict[str, tuple[float, float, float]]:
    """Run a batch of algos at one cache size, chunked at MAX_BATCH per
    subprocess (cachesim segfaults beyond 16 algos in a single invocation).

    Returns {our_algo_name: (req_mr, byte_mr, secs)}. cachesim threads the algos
    in a single batch over one trace load, so subdividing only costs the
    decompression overhead per chunk.
    """
    if not is_cachesim_available():
        raise RuntimeError(f"cachesim binary not built at {CACHESIM_BIN}")
    if not has_trace_file(trace_name):
        raise RuntimeError(
            f"trace {trace_name} not file-backed at {TRACE_FILES.get(trace_name)}")

    out: dict[str, tuple[float, float, float]] = {}
    for i in range(0, len(algos), MAX_BATCH):
        chunk = algos[i:i + MAX_BATCH]
        chunk_out = _run_one_batch(trace_name, chunk, cache_size, ignore_size, num_req)
        out.update(chunk_out)

    missing = set(algos) - set(out)
    if missing:
        raise RuntimeError(f"cachesim returned no MR for {missing}.")
    return out
