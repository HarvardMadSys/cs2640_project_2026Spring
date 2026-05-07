"""
Compare the ground-truth neighbor sets of two CodeSearchNet variants and plot,
for every query, how many of the top-k ground-truth vectors differ.

Usage:
  python plot_query_diff.py --compare col1,col2

Each collection name must map to a variant file:
  data/CodeSearchNet_neighbors_<variant>.npy   (shape: [n_queries, k])

The variant is extracted by stripping the first underscore-prefix and any
trailing _vN suffix, matching plot_query_neighbor_segments.py.

IMPORTANT: neighbor IDs in each variant index into that variant's dataset.
For "trimmed" variants, the first N rows of the original dataset were removed,
so the trimmed variant's IDs are offset by `original_size - variant_size` when
mapped back into the original index space. This script maps all neighbor IDs
to the original index space before comparing, so that identical ground truth
(modulo trimmed rows) shows up as identical.

For each query q, we compute:
    diff_count[q] = k - |set(remap(n1[q])) & set(remap(n2[q]))|
i.e. the number of ground-truth neighbors present in one variant but not the
other in the common (original) index space.

Outputs:
  query_diff_<variant1>_vs_<variant2>.pdf  -- bar chart, one bar per query
"""

import argparse
import os
import re
import sys

import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, "/home/yunjia/Desktop/scripts")
from plot_style import apply_style

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def collection_to_variant(collection_name):
    """
    Extract variant from collection name, stripping any trailing _vN suffix.
    e.g. codesearchnet_original    -> original
         codesearchnet_original_v0 -> original
         codesearchnet_trimmed2_v3 -> trimmed2
    """
    variant = collection_name.split("_", 1)[1] if "_" in collection_name else collection_name
    variant = re.sub(r"_v\d+$", "", variant)
    return variant


def load_neighbors(variant):
    path = os.path.join(DATA_DIR, f"CodeSearchNet_neighbors_{variant}.npy")
    if not os.path.exists(path):
        print(f"Error: neighbors file not found: {path}")
        sys.exit(1)
    nbrs = np.load(path).astype(np.int64)
    print(f"Loaded {variant}: shape={nbrs.shape} from {path}")
    return nbrs


def get_variant_size(variant):
    """Return the number of vectors in a variant's dataset file."""
    path = os.path.join(DATA_DIR, f"CodeSearchNet_dataset_{variant}.npy")
    if not os.path.exists(path):
        # trimmed0 has no dataset file but equals original
        if variant == "trimmed0":
            orig_path = os.path.join(DATA_DIR, "CodeSearchNet_dataset_original.npy")
            return np.load(orig_path, mmap_mode="r").shape[0]
        print(f"Error: dataset file not found for variant '{variant}': {path}")
        sys.exit(1)
    return np.load(path, mmap_mode="r").shape[0]


def remap_to_original(nbrs, variant, original_size):
    """
    Convert neighbor IDs from variant-local index space to the original
    dataset's index space.

    The trimmed variants drop the first `original_size - variant_size` rows,
    so adding that offset recovers the original index.
    """
    variant_size = get_variant_size(variant)
    shift = original_size - variant_size
    if shift < 0:
        print(f"Warning: variant '{variant}' is larger than original; not remapping.")
        return nbrs
    if shift == 0:
        return nbrs
    print(f"  Remapping {variant}: shift={shift} "
          f"(trimmed {shift}/{original_size} = {100*shift/original_size:.2f}%)")
    return nbrs + shift


def compute_diff(n1, n2):
    """
    n1, n2: [n_queries, k] arrays.
    Returns diff_count[q] = k - |set(n1[q]) & set(n2[q])|.
    """
    if n1.shape != n2.shape:
        print(f"Error: neighbor shapes differ: {n1.shape} vs {n2.shape}")
        sys.exit(1)
    n_queries, k = n1.shape
    diff_count = np.zeros(n_queries, dtype=np.int32)
    for q in range(n_queries):
        inter = np.intersect1d(n1[q], n2[q], assume_unique=False)
        diff_count[q] = k - inter.size
    return diff_count


def plot_diff(diff_count, variant1, variant2, k):
    apply_style()

    n_queries = diff_count.size
    order = np.argsort(-diff_count)
    sorted_diff = diff_count[order]

    fig, ax = plt.subplots(figsize=(12, 5))
    x = np.arange(n_queries)
    ax.bar(x, sorted_diff, width=1.0, color="#d95f02")

    ax.set_xlabel("Query (sorted by # differing neighbors, descending)")
    ax.set_ylabel(f"# differing neighbors (out of {k})")
    ax.set_title(f"Ground-truth neighbor differences: {variant1} vs {variant2}")
    ax.set_xlim(-0.5, n_queries - 0.5)
    y_top = max(1, int(diff_count.max())) * 1.1
    ax.set_ylim(0, y_top)

    mean_d = diff_count.mean()
    median_d = np.median(diff_count)
    zero_q = int((diff_count == 0).sum())
    ax.axhline(mean_d, color="black", linestyle="--", linewidth=1,
               label=f"mean = {mean_d:.1f}")
    ax.legend(loc="upper right")

    print(f"\nSummary over {n_queries} queries (k={k}):")
    print(f"  mean diff    : {mean_d:.2f}")
    print(f"  median diff  : {median_d:.1f}")
    print(f"  max diff     : {int(diff_count.max())}")
    print(f"  min diff     : {int(diff_count.min())}")
    print(f"  queries with identical neighbor sets: {zero_q}")

    plt.tight_layout()
    out_path = f"query_diff_{variant1}_vs_{variant2}.pdf"
    plt.savefig(out_path)
    print(f"\nSaved diff plot to {out_path}")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(
        description="Plot per-query ground-truth neighbor differences between two collections."
    )
    parser.add_argument("--compare", required=True,
                        help="Two collection names: col1,col2")
    args = parser.parse_args()

    parts = [p.strip() for p in args.compare.split(",")]
    if len(parts) != 2:
        print("--compare requires exactly two collection names: col1,col2")
        sys.exit(1)

    variant1 = collection_to_variant(parts[0])
    variant2 = collection_to_variant(parts[1])
    if variant1 == variant2:
        print(f"Warning: both collections map to the same variant '{variant1}'.")

    n1 = load_neighbors(variant1)
    n2 = load_neighbors(variant2)

    # Map both to the common original index space
    original_size = get_variant_size("original")
    print(f"\nOriginal dataset size: {original_size:,}")
    n1 = remap_to_original(n1, variant1, original_size)
    n2 = remap_to_original(n2, variant2, original_size)

    diff_count = compute_diff(n1, n2)
    plot_diff(diff_count, variant1, variant2, n1.shape[1])


if __name__ == "__main__":
    main()
