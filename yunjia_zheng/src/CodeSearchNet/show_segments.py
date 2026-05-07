"""
Show segment and index information for a Milvus collection,
or compare segment overlap between two collections.

Usage:
  python show_segments.py <collection_name>
  python show_segments.py --compare col1,col2
  python show_segments.py --compare col1,col2 --host 127.0.0.1 --port 19530
  python show_segments.py --force-compaction codesearchnet_original_v1

The --compare mode reads per-segment PK IDs directly from MinIO storage
(the Parquet binlog files), so it works without modifying Milvus source.
"""

import argparse
import os
import sys
import time

import numpy as np
import pyarrow.fs as pafs
import pyarrow.parquet as pq
from pymilvus import MilvusClient

sys.path.insert(0, "/home/yunjia/Desktop/scripts")
from plot_style import apply_style

# MinIO connection for reading segment binlogs directly
MINIO_ENDPOINT = "localhost:9000"
MINIO_ACCESS_KEY = "minioadmin"
MINIO_SECRET_KEY = "minioadmin"
MINIO_BUCKET = "a-bucket"
MINIO_ROOT_PATH = "files"

# The PK field ID for collections created by load_to_milvus.py
# (field "id" has fieldID=100)
PK_FIELD_ID = "100"


def get_s3fs():
    """Create a PyArrow S3 filesystem pointing at local MinIO."""
    return pafs.S3FileSystem(
        endpoint_override=f"http://{MINIO_ENDPOINT}",
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        scheme="http",
    )


def get_segment_list(client, collection_name):
    """Return list of (segment_id, num_rows) sorted by segment_id."""
    segments = client.list_persistent_segments(collection_name)
    return sorted(
        [(seg.segment_id, seg.num_rows) for seg in segments
         if seg.num_rows > 0],
        key=lambda x: x[0],
    )


def get_collection_id(client, collection_name):
    """Get the collection ID from the persistent segment info."""
    segments = client.list_persistent_segments(collection_name)
    if segments:
        return segments[0].collection_id
    return None


def read_segment_pks(fs, collection_id, partition_id, segment_id):
    """
    Read primary key IDs from a segment's Parquet binlog files in MinIO.

    The binlog path is: {root}/insert_log/{collectionID}/{partitionID}/{segmentID}/_data/
    The PK field (field ID 100) is stored in the first column group parquet file.
    """
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
            # This parquet file doesn't have the PK column (e.g. vector-only group)
            continue
        except Exception as e:
            print(f"  Warning: cannot read {fi.path}: {e}")
            continue

    return ids


def discover_partition_ids(fs, collection_id):
    """List partition IDs under a collection's insert_log path."""
    base = f"{MINIO_BUCKET}/{MINIO_ROOT_PATH}/insert_log/{collection_id}/"
    file_infos = fs.get_file_info(pafs.FileSelector(base, recursive=False))
    partition_ids = []
    for fi in file_infos:
        if fi.type == pafs.FileType.Directory:
            name = fi.path.rstrip("/").split("/")[-1]
            if name.isdigit():
                partition_ids.append(int(name))
    return partition_ids


def load_all_segment_pks(client, collection_name, segment_ids):
    """
    For each segment in segment_ids, read PKs from MinIO binlogs.
    Returns dict {segment_id: set of int IDs}.
    """
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
                print(f"  Segment {seg_id}: {len(pks):,} IDs")
                break
        else:
            print(f"  Segment {seg_id}: no binlog data found")

    return seg_pks


def show_collection(client, collection_name):
    """Print per-segment info for one collection."""
    segments = client.list_persistent_segments(collection_name)
    print(f"Collection: {collection_name}")
    print(f"Total segments: {len(segments)}")
    print("-" * 90)

    total_rows = 0
    for seg in segments:
        total_rows += seg.num_rows
        print(f"  Segment ID   : {seg.segment_id}")
        print(f"  Num Rows      : {seg.num_rows}")
        print(f"  State         : {seg.state_name}")
        print(f"  Level         : {seg.level_name}")
        print("-" * 90)

    print(f"Total rows across all segments: {total_rows}")

    indexes = client.list_indexes(collection_name)
    if indexes:
        print(f"\nCollection-level indexes:")
        for idx_name in indexes:
            idx = client.describe_index(collection_name, idx_name)
            print(f"  {idx}")


def compare_collections(client, name1, name2):
    """Compare two collections: compute per-segment overlap matrix and plot."""
    import matplotlib.pyplot as plt

    apply_style()

    for name in (name1, name2):
        if name not in client.list_collections():
            print(f"Collection '{name}' not found.")
            sys.exit(1)

    segs1 = get_segment_list(client, name1)
    segs2 = get_segment_list(client, name2)

    print(f"Collection '{name1}': {len(segs1)} segments")
    print(f"Collection '{name2}': {len(segs2)} segments")

    # Read PKs from MinIO binlogs
    print(f"\nReading segment PKs for '{name1}':")
    seg_ids1 = load_all_segment_pks(client, name1, [s[0] for s in segs1])
    print(f"\nReading segment PKs for '{name2}':")
    seg_ids2 = load_all_segment_pks(client, name2, [s[0] for s in segs2])

    # Filter to segments with data
    segs1 = [(sid, nr) for sid, nr in segs1 if sid in seg_ids1]
    segs2 = [(sid, nr) for sid, nr in segs2 if sid in seg_ids2]

    if not segs1 or not segs2:
        print("Error: no segment data found.")
        sys.exit(1)

    # Build overlap matrix
    n1 = len(segs1)
    n2 = len(segs2)
    overlap = np.zeros((n1, n2), dtype=np.int64)

    for i, (sid1, _) in enumerate(segs1):
        ids1 = seg_ids1[sid1]
        for j, (sid2, _) in enumerate(segs2):
            overlap[i, j] = len(ids1 & seg_ids2[sid2])

    # Print the matrix
    print(f"\nOverlap matrix ({n1} x {n2}):")
    col2_labels = [str(sid)[-6:] for sid, _ in segs2]
    header = "          " + "  ".join(f"{l:>8s}" for l in col2_labels)
    print(header)
    for i, (sid1, nr1) in enumerate(segs1):
        row_str = "  ".join(f"{overlap[i, j]:8d}" for j in range(n2))
        print(f"  {str(sid1)[-6:]:>6s}  {row_str}")

    # Plot heatmap
    crowded = n1 > 10 or n2 > 10
    if crowded:
        # Keep the figure compact so labels/title dominate instead of a huge matrix
        fig, ax = plt.subplots(figsize=(6, 5))
    else:
        fig, ax = plt.subplots(figsize=(max(6, n2 * 0.8 + 2), max(5, n1 * 0.6 + 2)))

    row_labels = [f"...{str(sid)[-6:]}\n({nr:,})" for sid, nr in segs1]
    col_labels = [f"...{str(sid)[-6:]}\n({nr:,})" for sid, nr in segs2]

    im = ax.imshow(overlap, cmap="YlOrRd", aspect="auto")
    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label("Shared vectors", fontsize=14)
    cbar.ax.tick_params(labelsize=12)

    # If either axis has more than 10 segments, hide per-segment ticks to avoid
    # clutter and use a coarser colorbar tick step.
    if crowded:
        ax.set_xticks([])
        ax.set_yticks([])

        # Coarser colorbar ticks: ~4 steps rounded to a nice number
        import matplotlib.ticker as mticker
        vmax = float(overlap.max()) if overlap.size else 0.0
        if vmax > 0:
            raw_step = vmax / 4.0
            mag = 10 ** int(np.floor(np.log10(raw_step)))
            for mult in (1, 2, 5, 10):
                if mult * mag >= raw_step:
                    step = mult * mag
                    break
            cbar.locator = mticker.MultipleLocator(step)
            cbar.update_ticks()
    else:
        ax.set_xticks(range(n2))
        ax.set_xticklabels(col_labels, fontsize=9)
        ax.set_yticks(range(n1))
        ax.set_yticklabels(row_labels, fontsize=9)

    ax.set_xlabel(name2, fontsize=14)
    ax.set_ylabel(name1, fontsize=14)
    ax.set_title("Per-segment vector overlap", fontsize=15)

    # Annotate cells with counts (only when layout is small enough to read them)
    if not crowded:
        for i in range(n1):
            for j in range(n2):
                val = overlap[i, j]
                if val > 0:
                    color = "white" if val > overlap.max() * 0.6 else "black"
                    ax.text(j, i, f"{val:,}", ha="center", va="center",
                            fontsize=8, color=color)

    # Re-enable spines for the heatmap
    for spine in ax.spines.values():
        spine.set_visible(True)

    plt.tight_layout()
    out_path = f"segment_overlap_{name1}_vs_{name2}.pdf"
    plt.savefig(out_path)
    print(f"\nSaved heatmap to {out_path}")
    plt.show()

    # Plot per-collection segment layout
    print(f"\nPlotting segment layout for '{name1}':")
    plot_segment_layout(name1, segs1, seg_ids1)
    print(f"\nPlotting segment layout for '{name2}':")
    plot_segment_layout(name2, segs2, seg_ids2)


DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def collection_to_variant(collection_name):
    """Extract variant name from collection name, e.g. codesearchnet_original -> original."""
    return collection_name.split("_", 1)[1] if "_" in collection_name else collection_name


def plot_segment_layout(collection_name, segs, seg_pks):
    """
    For a collection, plot which original-dataset vector indices each segment
    contains.  X-axis is the vector order (primary key = row index in the
    dataset file).  Each row is one segment, with a scatter/heatmap strip
    showing the density of IDs it holds.

    Produces: segment_layout_{collection_name}.pdf
    """
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors

    apply_style()

    variant = collection_to_variant(collection_name)
    dataset_path = os.path.join(DATA_DIR, f"CodeSearchNet_dataset_{variant}.npy")
    if not os.path.exists(dataset_path):
        print(f"  Warning: dataset file not found: {dataset_path}")
        return
    n_vectors = np.load(dataset_path, mmap_mode="r").shape[0]

    # Sort segments by their minimum ID so the plot reads naturally
    seg_order = []
    for sid, nr in segs:
        if sid in seg_pks:
            ids = seg_pks[sid]
            seg_order.append((sid, nr, min(ids)))
    seg_order.sort(key=lambda x: x[2])

    n_segs = len(seg_order)
    if n_segs == 0:
        return

    # Bin the IDs into histogram bins for a density strip
    n_bins = min(500, n_vectors)
    bin_edges = np.linspace(0, n_vectors, n_bins + 1)

    density = np.zeros((n_segs, n_bins), dtype=np.float32)
    for i, (sid, nr, _) in enumerate(seg_order):
        ids_arr = np.array(sorted(seg_pks[sid]))
        hist, _ = np.histogram(ids_arr, bins=bin_edges)
        density[i] = hist

    # Normalize each row by its max for visibility
    row_max = density.max(axis=1, keepdims=True)
    row_max[row_max == 0] = 1
    density_norm = density / row_max

    fig, ax = plt.subplots(figsize=(12, max(3, n_segs * 0.5 + 1.5)))

    # Use a colormap where 0 is white
    cmap = plt.cm.Blues.copy()
    cmap.set_under("white")
    im = ax.imshow(
        density_norm, aspect="auto", cmap=cmap,
        vmin=0.01, vmax=1.0,
        extent=[0, n_vectors, n_segs - 0.5, -0.5],
        interpolation="nearest",
    )

    if n_segs > 10:
        ax.set_yticks([])
    else:
        ax.set_yticks(range(n_segs))
        ax.set_yticklabels(
            [f"...{str(sid)[-6:]}\n({nr:,})" for sid, nr, _ in seg_order],
            fontsize=8,
        )
    ax.set_xlabel("Vector order in input dataset", fontsize=14)
    ax.set_ylabel("Segment", fontsize=14)
    ax.set_title(f"Segment layout: {collection_name}", fontsize=15)
    ax.tick_params(axis="x", labelsize=12)

    cbar = fig.colorbar(im, ax=ax, pad=0.02)
    cbar.set_label("Relative density", fontsize=14)
    cbar.ax.tick_params(labelsize=12)

    for spine in ax.spines.values():
        spine.set_visible(True)

    plt.tight_layout()
    out_path = f"segment_layout_{collection_name}.pdf"
    plt.savefig(out_path)
    print(f"  Saved segment layout to {out_path}")
    plt.show()


def force_compaction(client, collection_name, poll_interval=2.0, timeout=600.0):
    """
    Trigger a mix compaction on the collection and wait for it to finish.
    Prints before/after segment counts so you can see the effect.
    """
    if collection_name not in client.list_collections():
        print(f"Collection '{collection_name}' not found.")
        sys.exit(1)

    before = get_segment_list(client, collection_name)
    print(f"Before compaction: {len(before)} segments, "
          f"{sum(nr for _, nr in before):,} rows")

    print(f"Flushing '{collection_name}' …")
    client.flush(collection_name=collection_name)

    print(f"Triggering compaction on '{collection_name}' …")
    job_id = client.compact(collection_name=collection_name)
    print(f"  job_id = {job_id}")

    deadline = time.time() + timeout
    while True:
        state = client.get_compaction_state(job_id)
        print(f"  state = {state}")
        if str(state).lower() in ("completed", "compactionstate.completed"):
            break
        if time.time() > deadline:
            print(f"  Timed out waiting for compaction after {timeout}s")
            break
        time.sleep(poll_interval)

    after = get_segment_list(client, collection_name)
    print(f"After compaction:  {len(after)} segments, "
          f"{sum(nr for _, nr in after):,} rows")


def main():
    parser = argparse.ArgumentParser(
        description="Show per-segment info or compare collections."
    )
    parser.add_argument("collection", nargs="?", default=None,
                        help="Name of the collection to inspect")
    parser.add_argument("--compare", default=None,
                        help="Compare two collections: col1,col2")
    parser.add_argument("--force-compaction", default=None, metavar="COLLECTION",
                        help="Trigger a mix compaction on the named collection "
                             "and wait for it to finish")
    parser.add_argument("--host", default="localhost", help="Milvus host")
    parser.add_argument("--port", default="19530", help="Milvus port")
    args = parser.parse_args()

    uri = f"http://{args.host}:{args.port}"
    client = MilvusClient(uri=uri)

    if args.force_compaction:
        force_compaction(client, args.force_compaction)
        return

    if args.compare:
        parts = args.compare.split(",")
        if len(parts) != 2:
            print("--compare requires exactly two collection names: col1,col2")
            sys.exit(1)
        compare_collections(client, parts[0].strip(), parts[1].strip())
    elif args.collection:
        show_collection(client, args.collection)
    else:
        print("Provide a collection name, --compare col1,col2, or --force-compaction col")
        sys.exit(1)


if __name__ == "__main__":
    main()
