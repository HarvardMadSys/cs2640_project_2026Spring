"""Load wiki/info artifacts into local Milvus as collection 'wiki' with three
partitions: shared_partition, partition_A, partition_B.

Prereqs:
  - Milvus running (see ../setup/prepare_milvus.sh).
  - prepare_wiki_partitions.py has produced wiki_embeddings.npy,
    wiki_texts.parquet, partitions.parquet, meta.json under wiki/info/.
  - pip install pymilvus

Usage:
  python load_to_milvus.py                     # full load with defaults
  python load_to_milvus.py --drop              # drop & recreate collection
  python load_to_milvus.py --skip-insert       # just (re)build index / load

A vector can be in multiple partitions — the script inserts it into each
partition it belongs to (per the masks in partitions.parquet). Milvus does
not enforce PK uniqueness across partitions, so duplicates are allowed by
design and match the overlap structure from prepare_wiki_partitions.py.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from tqdm import tqdm

HERE = Path(__file__).resolve().parent
INFO_DIR = HERE / "info"

WIKI_EMB_PATH = INFO_DIR / "wiki_embeddings.npy"
WIKI_TEXTS_PATH = INFO_DIR / "wiki_texts.parquet"
PARTITIONS_PATH = INFO_DIR / "partitions.parquet"
META_PATH = INFO_DIR / "meta.json"

PARTITION_NAMES = ["shared_partition", "partition_A", "partition_B"]
MASK_COLS = ["in_shared", "in_A", "in_B"]

DEFAULT_URI = "http://localhost:19530"
DEFAULT_COLLECTION = "wiki"


def connect(uri: str):
    from pymilvus import MilvusClient
    print(f"Connecting to Milvus at {uri} ...")
    client = MilvusClient(uri=uri)
    print("  Connected.")
    return client


def create_collection(client, collection: str, dim: int):
    from pymilvus import DataType

    if client.has_collection(collection):
        print(f"  Collection '{collection}' already exists, dropping ...")
        client.drop_collection(collection)

    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("id",      DataType.INT64,        is_primary=True)
    schema.add_field("vector",  DataType.FLOAT_VECTOR, dim=dim)
    schema.add_field("wiki_id", DataType.INT64)

    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name  = "vector",
        index_type  = "HNSW",
        metric_type = "COSINE",
        params      = {"M": 16, "efConstruction": 200},
    )

    client.create_collection(
        collection_name = collection,
        schema          = schema,
        index_params    = index_params,
    )
    print(f"  Created collection '{collection}' (HNSW, COSINE, dim={dim}).")


def ensure_partitions(client, collection: str):
    existing = set(client.list_partitions(collection))
    for p in PARTITION_NAMES:
        if p not in existing:
            client.create_partition(collection, p)
            print(f"  Created partition '{p}'.")
        else:
            print(f"  Partition '{p}' already exists.")


def load_partition_masks() -> dict[str, np.ndarray]:
    tbl = pq.read_table(PARTITIONS_PATH)
    vec_ids = tbl.column("vec_id").to_numpy()
    if not np.array_equal(vec_ids, np.arange(len(vec_ids))):
        raise RuntimeError("partitions.parquet vec_ids must be 0..N-1 in order")
    masks = {}
    for pname, col in zip(PARTITION_NAMES, MASK_COLS):
        masks[pname] = tbl.column(col).to_numpy()
        print(f"  {pname}: {int(masks[pname].sum()):,} entities")
    return masks


def insert_all(
    client,
    collection: str,
    emb: np.ndarray,
    part_masks: dict[str, np.ndarray],
    read_batch: int,
    insert_batch: int,
):
    """Stream wiki_texts.parquet sequentially for wiki_id, dispatch rows into
    per-partition buffers, flush to Milvus when they reach insert_batch."""
    pf = pq.ParquetFile(WIKI_TEXTS_PATH)
    N = emb.shape[0]

    buffers: dict[str, list[dict]] = {p: [] for p in PARTITION_NAMES}
    counters: dict[str, int] = {p: 0 for p in PARTITION_NAMES}
    t_total = time.time()

    def flush(pname: str):
        if not buffers[pname]:
            return
        client.insert(collection_name=collection, data=buffers[pname], partition_name=pname)
        buffers[pname] = []

    pbar = tqdm(total=N, desc="Streaming parquet", unit="row")
    for batch in pf.iter_batches(batch_size=read_batch, columns=["vec_id", "wiki_id"]):
        vec_ids  = batch.column("vec_id").to_numpy().astype(np.int64)
        wiki_ids = batch.column("wiki_id").to_numpy().astype(np.int64)

        # Pre-fetch the whole batch of embeddings once (fast fancy indexing on mmap).
        batch_emb = np.asarray(emb[vec_ids])

        for pname, mask in part_masks.items():
            sel = mask[vec_ids]
            if not sel.any():
                continue
            idxs = np.nonzero(sel)[0]
            for j in idxs:
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

    print(f"  Inserted (per-partition rows): {counters}")
    print(f"  Total insert time: {time.time() - t_total:.1f}s")
    print("  Flushing ...")
    client.flush(collection)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uri", default=DEFAULT_URI)
    ap.add_argument("--collection", default=DEFAULT_COLLECTION)
    ap.add_argument("--read-batch", type=int, default=10_000,
                    help="Rows per parquet read-ahead batch.")
    ap.add_argument("--insert-batch", type=int, default=2_000,
                    help="Rows per Milvus insert call.")
    ap.add_argument("--drop", action="store_true",
                    help="Drop & recreate the collection before inserting.")
    ap.add_argument("--skip-insert", action="store_true",
                    help="Skip insertion (re)build index and load_collection only.")
    args = ap.parse_args()

    meta = json.loads(META_PATH.read_text())
    dim = int(meta["dim"])
    expected_total = int(meta["final_shared_size"]) + int(meta["partition_A_size"]) + int(meta["partition_B_size"])
    print(f"Meta: dim={dim}, num_wiki={meta['num_wiki']}, "
          f"sizes shared={meta['final_shared_size']:,} A={meta['partition_A_size']:,} "
          f"B={meta['partition_B_size']:,} (expected total rows with duplicates={expected_total:,})")

    client = connect(args.uri)

    if args.drop:
        create_collection(client, args.collection, dim)
    elif not client.has_collection(args.collection):
        create_collection(client, args.collection, dim)
    else:
        print(f"  Reusing existing collection '{args.collection}'.")

    ensure_partitions(client, args.collection)

    if not args.skip_insert:
        print("Loading partition masks ...")
        part_masks = load_partition_masks()

        print("Mmapping wiki embeddings ...")
        emb = np.load(WIKI_EMB_PATH, mmap_mode="r")
        print(f"  shape={emb.shape}, dtype={emb.dtype}")
        if emb.shape[1] != dim:
            raise RuntimeError(f"embedding dim mismatch: {emb.shape[1]} vs meta dim {dim}")

        insert_all(
            client, args.collection, emb, part_masks,
            read_batch=args.read_batch,
            insert_batch=args.insert_batch,
        )

    print("Loading collection into memory ...")
    client.load_collection(args.collection)

    stats = client.get_collection_stats(args.collection)
    print(f"  row_count = {stats.get('row_count', '?')} (expected {expected_total:,})")

    for p in PARTITION_NAMES:
        try:
            pstats = client.get_partition_stats(args.collection, p)
            print(f"  {p}: row_count = {pstats.get('row_count', '?')}")
        except Exception as e:
            print(f"  {p}: stats unavailable ({e})")

    print("Done.")


if __name__ == "__main__":
    main()
