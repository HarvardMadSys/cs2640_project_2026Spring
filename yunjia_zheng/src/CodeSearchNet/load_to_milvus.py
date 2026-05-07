"""
Load embeddings into local Milvus and run a recall benchmark.

Supports both original and trimmed data sets produced by prepare_from_VIBE.py.

Prerequisites:
  1. Run prepare_from_VIBE.py first to produce data/ files.
  2. Start local Milvus (built in /scratch/yunjia/milvus):
       cd /scratch/yunjia/milvus && ./bin/milvus run standalone &
  3. pip install pymilvus

Usage:
  python load_to_milvus.py --variant original
  python load_to_milvus.py --variant trimmed
  python load_to_milvus.py --variant original --skip-insert --ef 400
"""

import argparse
import glob
import os
import re
import numpy as np
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DATA_DIR        = os.path.join(os.path.dirname(__file__), "data")
DEFAULT_URI     = "http://localhost:19530"
DIM             = 768
TOP_K           = 100
INSERT_BATCH    = 10_000


# ---------------------------------------------------------------------------
# Load data files produced by prepare_from_VIBE.py
# ---------------------------------------------------------------------------

def discover_variants() -> list[str]:
    """Scan data/ for available variants based on CodeSearchNet_dataset_*.npy files."""
    pattern = os.path.join(DATA_DIR, "CodeSearchNet_dataset_*.npy")
    variants = sorted(
        re.match(r"CodeSearchNet_dataset_(.+)\.npy", os.path.basename(f)).group(1)
        for f in glob.glob(pattern)
    )
    return variants


def load_data(variant: str):
    corpus_path = os.path.join(DATA_DIR, f"CodeSearchNet_dataset_{variant}.npy")
    query_path  = os.path.join(DATA_DIR, f"CodeSearchNet_queries_{variant}.npy")
    nbrs_path   = os.path.join(DATA_DIR, f"CodeSearchNet_neighbors_{variant}.npy")
    dist_path   = os.path.join(DATA_DIR, f"CodeSearchNet_distances_{variant}.npy")
    for p in (corpus_path, query_path, nbrs_path, dist_path):
        if not os.path.exists(p):
            raise FileNotFoundError(
                f"{p} not found. Run prepare_from_VIBE.py first."
            )
    corpus    = np.load(corpus_path)
    queries   = np.load(query_path)
    neighbors = np.load(nbrs_path)
    distances = np.load(dist_path)
    return corpus, queries, neighbors, distances


# ---------------------------------------------------------------------------
# Milvus helpers
# ---------------------------------------------------------------------------

def connect(uri: str):
    from pymilvus import MilvusClient
    print(f"Connecting to Milvus at {uri} …")
    client = MilvusClient(uri=uri)
    print("  Connected.")
    return client


def create_collection(client, collection: str):
    from pymilvus import DataType
    if client.has_collection(collection):
        print(f"  Collection '{collection}' already exists, dropping …")
        client.drop_collection(collection)

    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("id",     DataType.INT64,        is_primary=True)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=DIM)

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
    print(f"  Created collection '{collection}' (HNSW, COSINE, dim={DIM}).")


def report_visible(client, collection: str, expected: int, label: str):
    """
    Ask Milvus how many rows it currently reports for `collection` and print
    visible / expected. Used to diagnose whether the QueryNode has all the
    inserted rows loaded at the time we are about to query.
    """
    try:
        stats = client.get_collection_stats(collection_name=collection)
        # MilvusClient returns either {"row_count": N} or {"row_count": "N"}.
        visible = int(stats.get("row_count", -1))
    except Exception as e:
        print(f"  [{label}] get_collection_stats failed: {e}")
        return
    diff = expected - visible
    pct  = (visible / expected * 100.0) if expected > 0 else float("nan")
    flag = "OK" if diff == 0 else f"MISSING {diff:,}"
    print(f"  [{label}] visible={visible:,} / expected={expected:,} "
          f"({pct:.2f}%)  {flag}")


def insert_corpus(client, collection: str, corpus: np.ndarray):
    N = corpus.shape[0]
    print(f"Inserting {N:,} vectors in batches of {INSERT_BATCH:,} …")
    for start in tqdm(range(0, N, INSERT_BATCH), desc="  inserting"):
        end  = min(start + INSERT_BATCH, N)
        rows = [
            {"id": int(i), "vector": corpus[i].tolist()}
            for i in range(start, end)
        ]
        client.insert(collection_name=collection, data=rows)
    print("  Flushing …")
    client.flush(collection_name=collection)
    print(f"  Inserted and flushed {N:,} vectors.")
    report_visible(client, collection, N, "after flush")


def load_collection(client, collection: str, expected: int | None = None):
    print("Loading collection into memory …")
    client.load_collection(collection)
    print("  Loaded.")
    if expected is not None:
        report_visible(client, collection, expected, "after load_collection")


# ---------------------------------------------------------------------------
# Recall benchmark
# ---------------------------------------------------------------------------

def run_recall(
    client,
    collection: str,
    queries: np.ndarray,
    gt_neighbors: np.ndarray,
    top_k: int = TOP_K,
    ef: int = 200,
    query_batch: int = 100,
) -> float:
    """
    Query Milvus and compute Recall@K against ground-truth neighbors.
    Returns mean recall over all queries.
    """
    Q = queries.shape[0]
    total_hits = 0

    print(f"Running recall benchmark (ef={ef}, top_k={top_k}) …")
    for start in tqdm(range(0, Q, query_batch), desc="  querying"):
        end     = min(start + query_batch, Q)
        q_batch = queries[start:end].tolist()

        results = client.search(
            collection_name = collection,
            data            = q_batch,
            anns_field      = "vector",
            search_params   = {"metric_type": "COSINE", "params": {"ef": ef}},
            limit           = top_k,
            output_fields   = [],
        )

        for i, res in enumerate(results):
            returned_ids = {hit["id"] for hit in res}
            gt_ids       = set(gt_neighbors[start + i].tolist())
            total_hits  += len(returned_ids & gt_ids)

    recall = total_hits / (Q * top_k)
    return recall


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Load embeddings into local Milvus and benchmark recall."
    )
    available = discover_variants()
    parser.add_argument("--variant",     default="original",
                        help=f"Which data set to use (default: original). "
                             f"Available: {', '.join(available) if available else 'none found'}")
    parser.add_argument("--version",     default=None,
                        help="Optional version tag appended to the default collection "
                             "name, e.g. --version v2 -> codesearchnet_<variant>_v2. "
                             "Ignored if --collection is set explicitly.")
    parser.add_argument("--list",        action="store_true",
                        help="List available variants and exit")
    parser.add_argument("--uri",         default=DEFAULT_URI,
                        help="Milvus URI (default: http://localhost:19530)")
    parser.add_argument("--collection",  default=None,
                        help="Collection name (default: codesearchnet_<variant>[_<version>])")
    parser.add_argument("--skip-insert", action="store_true",
                        help="Skip insertion if collection already populated")
    parser.add_argument("--ef",          type=int, default=200,
                        help="HNSW ef search parameter (default: 200)")
    parser.add_argument("--drop",        default=None, metavar="COLLECTION",
                        help="Drop the named collection and exit")
    args = parser.parse_args()

    if args.list:
        client = connect(args.uri)
        collections = client.list_collections()
        print("Milvus collections:")
        if collections:
            for c in sorted(collections):
                print(f"  {c}")
        else:
            print("  (none)")
        return

    if args.drop is not None:
        client = connect(args.uri)
        if client.has_collection(args.drop):
            client.drop_collection(args.drop)
            print(f"Dropped collection '{args.drop}'.")
        else:
            print(f"Collection '{args.drop}' does not exist.")
        return

    if args.collection is None:
        args.collection = f"codesearchnet_{args.variant}"
        if args.version:
            args.collection = f"{args.collection}_{args.version}"

    corpus, queries, neighbors, distances = load_data(args.variant)
    print(f"Data loaded ({args.variant}): corpus={corpus.shape}, "
          f"queries={queries.shape}, neighbors={neighbors.shape}")

    client = connect(args.uri)

    if not args.skip_insert:
        create_collection(client, args.collection)
        insert_corpus(client, args.collection, corpus)

    load_collection(client, args.collection, expected=corpus.shape[0])

    report_visible(client, args.collection, corpus.shape[0], "before search")

    recall = run_recall(
        client, args.collection, queries, neighbors,
        top_k=TOP_K, ef=args.ef,
    )
    print(f"\nRecall@{TOP_K} = {recall:.4f}  ({recall*100:.2f}%)")

    client.release_collection(args.collection)
    print("Done.")


if __name__ == "__main__":
    main()
