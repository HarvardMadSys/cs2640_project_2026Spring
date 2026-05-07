"""Load both compact and nocompact versions of the three wiki variants into a
single Milvus instance, one flavor at a time, with the three variants of each
flavor running concurrently.

For each variant in {wiki_QC02_SS02, wiki_QC02_SS08, wiki_QC08_SS08} this script
builds two collections, in two phases:

  Phase 1 (default):
    <variant>_nocompact — the per-collection property
                          collection.autocompaction.enabled=false is set
                          BEFORE inserts, so the small L1 segments produced by
                          insertion persist across restarts and are never
                          merged. The three nocompact builds run in parallel.

  Phase 2 (after Phase 1 finishes):
    <variant>           — auto-compaction stays at the global yaml default
                          (enabled), so mix-compaction will merge segments
                          toward dataCoord.segment.maxSize over time. The
                          three compact builds run in parallel.

The two flavors share the same raw data under <variant>_info/ and the same
HNSW spec, only the post-flush compaction policy differs. Each thread uses
its own MilvusClient so a failure in one cell does not affect the others.

Usage:
  python load_both_compact_nocompact_variants.py
  python load_both_compact_nocompact_variants.py --variants wiki_QC02_SS02
  python load_both_compact_nocompact_variants.py --flavors nocompact
  python load_both_compact_nocompact_variants.py --sequential
  python load_both_compact_nocompact_variants.py --max-workers 3
  python load_both_compact_nocompact_variants.py --skip-insert
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from tqdm import tqdm

HERE = Path(__file__).resolve().parent

DEFAULT_VARIANTS = ["wiki_QC02_SS02", "wiki_QC02_SS08", "wiki_QC08_SS08"]
DEFAULT_FLAVORS  = ["nocompact", "compact"]   # nocompact first, then compact.
PARTITION_NAMES  = ["shared_partition", "partition_A", "partition_B"]
MASK_COLS        = ["in_shared", "in_A", "in_B"]
DEFAULT_URI      = "http://localhost:19530"

_PRINT_LOCK = threading.Lock()


def vlog(tag: str, msg: str) -> None:
    with _PRINT_LOCK:
        print(f"[{tag}] {msg}", flush=True)


def info_paths(variant: str) -> dict:
    info = HERE / f"{variant}_info"
    return {
        "info":       info,
        "emb":        info / "wiki_embeddings.npy",
        "texts":      info / "wiki_texts.parquet",
        "partitions": info / "partitions.parquet",
        "meta":       info / "meta.json",
    }


def collection_name(variant: str, flavor: str) -> str:
    if flavor == "compact":
        return variant
    if flavor == "nocompact":
        return f"{variant}_nocompact"
    raise ValueError(f"unknown flavor {flavor!r}")


def create_collection(client, tag: str, collection: str, dim: int):
    from pymilvus import DataType
    if client.has_collection(collection):
        vlog(tag, f"collection '{collection}' already exists, dropping ...")
        client.drop_collection(collection)

    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("id",      DataType.INT64,        is_primary=True)
    schema.add_field("vector",  DataType.FLOAT_VECTOR, dim=dim)
    schema.add_field("wiki_id", DataType.INT64)

    idx = client.prepare_index_params()
    idx.add_index(
        field_name="vector",
        index_type="HNSW",
        metric_type="COSINE",
        params={"M": 16, "efConstruction": 200},
    )

    client.create_collection(
        collection_name=collection,
        schema=schema,
        index_params=idx,
    )
    vlog(tag, f"created '{collection}' (HNSW, COSINE, dim={dim}).")


def freeze_compaction(client, tag: str, collection: str):
    """Disable auto-compaction at the collection level. Idempotent."""
    keys = [
        "collection.autocompaction.enabled",
        "collection.compaction.enabled",
    ]
    for k in keys:
        try:
            client.alter_collection_properties(
                collection_name=collection,
                properties={k: "false"},
            )
            vlog(tag, f"set property {k}=false on '{collection}'.")
            return
        except Exception as e:
            vlog(tag, f"property '{k}' rejected: {e}")
    vlog(tag,
         f"WARN: could not disable auto-compaction on '{collection}'. "
         f"Verify with show_segments after a restart that the small L1 "
         f"segments are not being merged.")


def ensure_partitions(client, tag: str, collection: str):
    existing = set(client.list_partitions(collection))
    for p in PARTITION_NAMES:
        if p not in existing:
            client.create_partition(collection, p)
            vlog(tag, f"created partition '{p}'.")


def load_part_masks(tag: str, partitions_path: Path) -> dict[str, np.ndarray]:
    tbl = pq.read_table(partitions_path)
    vec_ids = tbl.column("vec_id").to_numpy()
    if not np.array_equal(vec_ids, np.arange(len(vec_ids))):
        raise RuntimeError(f"{partitions_path} vec_ids must be 0..N-1 in order")
    masks = {}
    for pname, col in zip(PARTITION_NAMES, MASK_COLS):
        masks[pname] = tbl.column(col).to_numpy()
        vlog(tag, f"  {pname}: {int(masks[pname].sum()):,} entities")
    return masks


def insert_all(
    client,
    tag: str,
    collection: str,
    emb: np.ndarray,
    texts_path: Path,
    part_masks: dict[str, np.ndarray],
    read_batch: int,
    insert_batch: int,
    bar_position: int,
):
    pf = pq.ParquetFile(texts_path)
    N = emb.shape[0]
    buffers  = {p: [] for p in PARTITION_NAMES}
    counters = {p: 0  for p in PARTITION_NAMES}
    t0 = time.time()

    def flush(pname: str):
        if buffers[pname]:
            client.insert(
                collection_name=collection,
                data=buffers[pname],
                partition_name=pname,
            )
            buffers[pname] = []

    pbar = tqdm(
        total=N,
        desc=tag,
        unit="row",
        position=bar_position,
        leave=True,
        mininterval=1.0,
    )
    for batch in pf.iter_batches(batch_size=read_batch, columns=["vec_id", "wiki_id"]):
        vec_ids  = batch.column("vec_id").to_numpy().astype(np.int64)
        wiki_ids = batch.column("wiki_id").to_numpy().astype(np.int64)
        batch_emb = np.asarray(emb[vec_ids])
        for pname, mask in part_masks.items():
            sel = mask[vec_ids]
            if not sel.any():
                continue
            for j in np.nonzero(sel)[0]:
                buffers[pname].append({
                    "id":      int(vec_ids[j]),
                    "vector":  batch_emb[j].tolist(),
                    "wiki_id": int(wiki_ids[j]),
                })
                if len(buffers[pname]) >= insert_batch:
                    flush(pname)
                counters[pname] += 1
        pbar.update(len(vec_ids))
    pbar.close()
    for p in PARTITION_NAMES:
        flush(p)

    vlog(tag, f"per-partition rows: {counters}")
    vlog(tag, f"insert wall-time: {time.time() - t0:.1f}s, flushing ...")
    client.flush(collection)


def build_one(variant: str, flavor: str, args, bar_position: int = 0):
    """Full build pipeline for one (variant, flavor) cell on its own thread."""
    paths = info_paths(variant)
    if not paths["meta"].exists():
        vlog(f"{variant}/{flavor}", f"SKIP: missing {paths['meta']}")
        return

    meta = json.loads(paths["meta"].read_text())
    dim  = int(meta["dim"])
    collection = collection_name(variant, flavor)
    tag = collection

    vlog(tag,
         f"start  flavor={flavor} dim={dim} num_wiki={meta['num_wiki']:,} "
         f"shared={meta['final_shared_size']:,} "
         f"A={meta['partition_A_size']:,} B={meta['partition_B_size']:,}")

    from pymilvus import MilvusClient
    client = MilvusClient(uri=args.uri)

    if not args.skip_insert:
        create_collection(client, tag, collection, dim)
        if flavor == "nocompact":
            freeze_compaction(client, tag, collection)
        ensure_partitions(client, tag, collection)

        masks = load_part_masks(tag, paths["partitions"])

        vlog(tag, f"mmapping {paths['emb'].name} ...")
        emb = np.load(paths["emb"], mmap_mode="r")
        vlog(tag, f"embedding shape={emb.shape}, dtype={emb.dtype}")
        if emb.shape[1] != dim:
            raise RuntimeError(
                f"{tag}: embedding dim {emb.shape[1]} != meta dim {dim}")

        insert_all(
            client, tag, collection, emb, paths["texts"], masks,
            read_batch=args.read_batch,
            insert_batch=args.insert_batch,
            bar_position=bar_position,
        )
    else:
        if not client.has_collection(collection):
            create_collection(client, tag, collection, dim)
            if flavor == "nocompact":
                freeze_compaction(client, tag, collection)
            ensure_partitions(client, tag, collection)
        elif flavor == "nocompact":
            freeze_compaction(client, tag, collection)

    if args.load:
        vlog(tag, f"load_collection('{collection}') ...")
        client.load_collection(collection)
        stats = client.get_collection_stats(collection)
        vlog(tag, f"row_count = {stats.get('row_count', '?')}")
        for p in PARTITION_NAMES:
            try:
                ps = client.get_partition_stats(collection, p)
                vlog(tag, f"  {p}: row_count = {ps.get('row_count', '?')}")
            except Exception as e:
                vlog(tag, f"  {p}: stats unavailable ({e})")
    else:
        vlog(tag, f"done (not loaded). "
                  f"client.load_collection('{collection}') when ready.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uri",          default=DEFAULT_URI)
    ap.add_argument("--variants",     nargs="+", default=DEFAULT_VARIANTS,
                    help="Subset of variants to build. Default: all three.")
    ap.add_argument("--flavors",      nargs="+", default=DEFAULT_FLAVORS,
                    choices=DEFAULT_FLAVORS,
                    help="Which flavors to build. Default: both.")
    ap.add_argument("--read-batch",   type=int, default=10_000,
                    help="Rows per parquet read-ahead batch.")
    ap.add_argument("--insert-batch", type=int, default=2_000,
                    help="Rows per Milvus insert call per partition.")
    ap.add_argument("--skip-insert",  action="store_true",
                    help="Skip insertion. Only ensures collection + property.")
    ap.add_argument("--load",         action="store_true",
                    help="Call load_collection on each built collection. "
                         "Off by default because the six wiki collections do "
                         "not fit together in the 96 GiB cgroup.")
    ap.add_argument("--sequential",   action="store_true",
                    help="Build cells one at a time. Default is parallel.")
    ap.add_argument("--max-workers",  type=int, default=None,
                    help="Override thread pool size. Default = "
                         "len(variants) * len(flavors).")
    args = ap.parse_args()

    print(f"Connecting target: {args.uri}", flush=True)
    print(f"flavors (in order): {args.flavors}", flush=True)
    print(f"variants per flavor: {args.variants}", flush=True)
    if args.sequential:
        print("mode: sequential within each flavor (and flavors run in order)",
              flush=True)
    else:
        print("mode: variants run in parallel within a flavor; "
              "next flavor starts only after the previous flavor's builds finish",
              flush=True)

    all_errs: list[tuple[str, BaseException]] = []
    for flavor in args.flavors:
        cells = [(v, flavor) for v in args.variants]
        print(f"\n=== Phase: flavor={flavor} ({len(cells)} cells) ===",
              flush=True)
        t_phase = time.time()

        if args.sequential or len(cells) == 1:
            for i, (v, f) in enumerate(cells):
                try:
                    build_one(v, f, args, bar_position=i)
                except Exception as e:
                    vlog(collection_name(v, f), f"FAILED: {e}")
                    all_errs.append((collection_name(v, f), e))
        else:
            n = args.max_workers or len(cells)
            with ThreadPoolExecutor(max_workers=n) as ex:
                futures = {
                    ex.submit(build_one, v, f, args, i): (v, f)
                    for i, (v, f) in enumerate(cells)
                }
                for fut in as_completed(futures):
                    v, f = futures[fut]
                    tag = collection_name(v, f)
                    try:
                        fut.result()
                        vlog(tag, "done.")
                    except Exception as e:
                        vlog(tag, f"FAILED: {e}")
                        all_errs.append((tag, e))

        print(f"=== Phase flavor={flavor} finished in "
              f"{time.time() - t_phase:.1f}s ===", flush=True)

    if all_errs:
        print(f"\n{len(all_errs)} cell(s) failed across all phases:",
              flush=True)
        for tag, e in all_errs:
            print(f"  {tag}: {e}", flush=True)
        raise SystemExit(1)

    print("\nAll done.", flush=True)


if __name__ == "__main__":
    main()
