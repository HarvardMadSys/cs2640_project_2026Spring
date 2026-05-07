"""
For a given Milvus collection, plot how the 100 ground-truth neighbors of each
benchmark query are distributed across the collection's segments.

Usage:
  python plot_query_neighbor_belongs_to_segments.py <collection_name>
  python plot_query_neighbor_belongs_to_segments.py <collection_name> --host 127.0.0.1 --port 19530

The script:
  1. Loads per-segment primary keys from MinIO binlogs (same path as
     show_segments.py).
  2. Loads the ground-truth neighbors file for the matching variant from
     ./data/CodeSearchNet_neighbors_<variant>.npy (shape: [n_queries, 100]).
  3. For every query, counts how many of its 100 neighbors lie in each
     segment, producing a [n_queries, n_segments] matrix.
  4. Saves a stacked bar chart showing this distribution.
"""

import argparse
import os
import sys

import numpy as np
import pyarrow.fs as pafs
import pyarrow.parquet as pq
import matplotlib.pyplot as plt
from pymilvus import MilvusClient

sys.path.insert(0, "/home/yunjia/Desktop/scripts")
from plot_style import apply_style

# MinIO connection for reading segment binlogs directly
MINIO_ENDPOINT = "localhost:9000"
MINIO_ACCESS_KEY = "minioadmin"
MINIO_SECRET_KEY = "minioadmin"
MINIO_BUCKET = "a-bucket"
MINIO_ROOT_PATH = "files"

PK_FIELD_ID = "100"

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def get_s3fs():
    return pafs.S3FileSystem(
        endpoint_override=f"http://{MINIO_ENDPOINT}",
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        scheme="http",
    )


def get_segment_list(client, collection_name):
    segments = client.list_persistent_segments(collection_name)
    keep = []
    for seg in segments:
        if seg.num_rows <= 0:
            continue
        level = getattr(seg, "level_name", "") or ""
        state = getattr(seg, "state_name", "") or ""
        if level == "L0" or state in ("Dropped", "Dropping"):
            print(f"  skipping segment {seg.segment_id} "
                  f"(state={state}, level={level}, rows={seg.num_rows})")
            continue
        keep.append((seg.segment_id, seg.num_rows))
    return sorted(keep, key=lambda x: x[0])


def get_collection_id(client, collection_name):
    segments = client.list_persistent_segments(collection_name)
    if segments:
        return segments[0].collection_id
    return None


def discover_partition_ids(fs, collection_id):
    base = f"{MINIO_BUCKET}/{MINIO_ROOT_PATH}/insert_log/{collection_id}/"
    file_infos = fs.get_file_info(pafs.FileSelector(base, recursive=False))
    partition_ids = []
    for fi in file_infos:
        if fi.type == pafs.FileType.Directory:
            name = fi.path.rstrip("/").split("/")[-1]
            if name.isdigit():
                partition_ids.append(int(name))
    return partition_ids


def read_segment_pks(fs, collection_id, partition_id, segment_id):
    base = f"{MINIO_BUCKET}/{MINIO_ROOT_PATH}/insert_log/{collection_id}/{partition_id}/{segment_id}/_data/"
    try:
        file_infos = fs.get_file_info(pafs.FileSelector(base, recursive=False))
    except Exception as e:
        print(f"  Warning: cannot list {base}: {e}")
        return set()

    ids = set()
    for fi in file_infos:
        if not fi.path.endswith(".parquet"):
            continue
        try:
            t = pq.read_table(fi.path, columns=[PK_FIELD_ID], filesystem=fs)
            ids.update(t.column(PK_FIELD_ID).to_pylist())
        except KeyError:
            continue
        except Exception as e:
            print(f"  Warning: cannot read {fi.path}: {e}")
            continue
    return ids


def load_all_segment_pks(client, collection_name, segment_ids):
    fs = get_s3fs()
    collection_id = get_collection_id(client, collection_name)
    if collection_id is None:
        print(f"Error: cannot determine collection ID for '{collection_name}'")
        sys.exit(1)

    partition_ids = discover_partition_ids(fs, collection_id)
    if not partition_ids:
        print(f"Error: no partitions found for collection {collection_id}")
        sys.exit(1)

    seg_pks = {}
    for seg_id in segment_ids:
        for part_id in partition_ids:
            pks = read_segment_pks(fs, collection_id, part_id, seg_id)
            if pks:
                seg_pks[seg_id] = pks
                print(f"  Segment {seg_id}: {len(pks):,} IDs, "
                      f"range=[{min(pks):,}, {max(pks):,}]")
                break
        else:
            print(f"  Segment {seg_id}: no binlog data found")
    return seg_pks


def collection_to_variant(collection_name):
    """
    Extract variant from collection name, stripping any trailing _vN suffix.
    e.g. codesearchnet_original    -> original
         codesearchnet_original_v0 -> original
         codesearchnet_trimmed2_v3 -> trimmed2
    """
    import re
    variant = collection_name.split("_", 1)[1] if "_" in collection_name else collection_name
    variant = re.sub(r"_v\d+$", "", variant)
    return variant


def compute_query_segment_counts(neighbors, seg_order, seg_pks):
    """
    neighbors: [n_queries, k] array of ground-truth neighbor IDs.
    seg_order: list of (segment_id, num_rows, min_id) sorted.
    seg_pks:   {segment_id: set(ids)}.

    Returns counts array of shape [n_queries, n_segs] where counts[q, s] is
    how many of query q's neighbors lie in segment s.
    """
    n_queries, k = neighbors.shape
    n_segs = len(seg_order)
    counts = np.zeros((n_queries, n_segs), dtype=np.int32)

    # Build a lookup: id -> segment index (vectorized per segment).
    for s_idx, (sid, _, _) in enumerate(seg_order):
        pks = seg_pks[sid]
        # Use np.isin with the sorted array of PKs for speed
        pk_arr = np.fromiter(pks, dtype=np.int64, count=len(pks))
        pk_arr.sort()
        # neighbors flat search
        flat = neighbors.reshape(-1)
        mask = np.isin(flat, pk_arr, assume_unique=False)
        mask = mask.reshape(n_queries, k)
        counts[:, s_idx] = mask.sum(axis=1)

    return counts


def plot_stacked_bar(counts, seg_order, collection_name, k):
    """Stacked bar: each query is a bar, colored by segment share."""
    apply_style()
    plt.rcParams.update({
        "axes.titlesize":  19,
        "axes.labelsize":  17,
        "xtick.labelsize": 16,
        "ytick.labelsize": 16,
        "legend.fontsize": 15,
        "legend.title_fontsize": 15,
    })
    n_queries, n_segs = counts.shape

    # Sort queries by the segment that holds the majority of their neighbors,
    # then by that count, to make banding visible.
    dominant = counts.argmax(axis=1)
    dominant_count = counts.max(axis=1)
    order = np.lexsort((-dominant_count, dominant))
    counts_sorted = counts[order]

    fig, ax = plt.subplots(figsize=(12, 5))
    bottom = np.zeros(n_queries, dtype=np.int32)
    cmap = plt.cm.tab20 if n_segs <= 20 else plt.cm.viridis
    x = np.arange(n_queries)
    for s_idx in range(n_segs):
        sid, nr, _ = seg_order[s_idx]
        color = cmap(s_idx / max(n_segs - 1, 1))
        ax.bar(x, counts_sorted[:, s_idx], bottom=bottom,
               width=1.0, color=color,
               label=f"{s_idx + 1} ({nr / 1e6:.2f}M)")
        bottom += counts_sorted[:, s_idx]

    ax.set_xlabel("Query (sorted by dominant segment)")
    ax.set_ylabel(f"# neighbors (out of {k})")
    ax.set_title(f"Per-query neighbor distribution, {collection_name}")
    ax.set_xlim(-0.5, n_queries - 0.5)
    ax.set_ylim(0, k)
    ax.legend(loc="center left", bbox_to_anchor=(1.01, 0.5),
              fontsize=14, title="Segment", title_fontsize=15)

    plt.tight_layout()
    out_path = f"query_neighbor_segments_stacked_{collection_name}.pdf"
    plt.savefig(out_path, bbox_inches="tight")
    print(f"Saved stacked bar to {out_path}")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(
        description="Plot per-query neighbor distribution across segments."
    )
    parser.add_argument("collection", help="Name of the Milvus collection")
    parser.add_argument("--host", default="localhost", help="Milvus host")
    parser.add_argument("--port", default="19530", help="Milvus port")
    args = parser.parse_args()

    uri = f"http://{args.host}:{args.port}"
    client = MilvusClient(uri=uri)

    if args.collection not in client.list_collections():
        print(f"Collection '{args.collection}' not found.")
        sys.exit(1)

    state = client.get_load_state(args.collection).get("state")
    if "Loaded" not in str(state):
        print(f"Collection '{args.collection}' is {state}; loading …")
        client.load_collection(args.collection)
        print(f"  Loaded.")

    # Load neighbors file matching the variant
    variant = collection_to_variant(args.collection)
    neighbors_path = os.path.join(DATA_DIR, f"CodeSearchNet_neighbors_{variant}.npy")
    if not os.path.exists(neighbors_path):
        print(f"Error: neighbors file not found: {neighbors_path}")
        sys.exit(1)
    neighbors = np.load(neighbors_path).astype(np.int64)
    print(f"Loaded neighbors: shape={neighbors.shape} from {neighbors_path}")

    # Load segment PKs
    segs = get_segment_list(client, args.collection)
    print(f"\nCollection '{args.collection}': {len(segs)} segments with data")
    print("Reading segment PKs from MinIO:")
    seg_pks = load_all_segment_pks(client, args.collection, [s[0] for s in segs])

    segs = [(sid, nr) for sid, nr in segs if sid in seg_pks]
    if not segs:
        print("Error: no segment binlog data found.")
        sys.exit(1)

    # Order segments by their minimum PK so the plots read naturally.
    seg_order = []
    for sid, nr in segs:
        seg_order.append((sid, nr, min(seg_pks[sid])))
    seg_order.sort(key=lambda x: x[2])

    # Compute counts
    print("\nComputing per-query segment counts...")
    counts = compute_query_segment_counts(neighbors, seg_order, seg_pks)

    k = neighbors.shape[1]
    total_in_seg = counts.sum()
    total_possible = neighbors.size
    print(
        f"Neighbors found in tracked segments: {total_in_seg:,} / {total_possible:,} "
        f"({100.0 * total_in_seg / total_possible:.1f}%)"
    )

    # Summary per segment
    print("\nPer-segment totals (sum across all queries):")
    for (sid, nr, _), c in zip(seg_order, counts.sum(axis=0)):
        print(f"  ...{str(sid)[-6:]} ({nr:,} rows): {int(c):,} neighbor hits")

    # Plot
    plot_stacked_bar(counts, seg_order, args.collection, k)


if __name__ == "__main__":
    main()
