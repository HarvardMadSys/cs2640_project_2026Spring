"""
Plot the "hotness" of vectors in a CodeSearchNet variant: for every vector in
the dataset, count how many benchmark queries include it in their top-100
ground-truth neighbors. Produces two plots:

  1. Top-500 hottest vectors, sorted descending (bar chart).
  2. CDF of hotness across all vectors in the dataset.

Usage:
  python plot_vector_hotness.py <collection_name>

The collection name is mapped to a variant the same way as the other scripts:
strip the first underscore-prefix and any trailing _vN suffix, then load
  data/CodeSearchNet_neighbors_<variant>.npy     (shape: [n_queries, k])
  data/CodeSearchNet_dataset_<variant>.npy       (shape: [n_vectors, d])
"""

import argparse
import os
import re
import sys

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

sys.path.insert(0, "/home/yunjia/Desktop/scripts")
from plot_style import apply_style

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

TOP_N = 1000


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
    print(f"Loaded neighbors: shape={nbrs.shape} from {path}")
    return nbrs


def get_n_vectors(variant):
    """Return the number of vectors in the variant's dataset file."""
    path = os.path.join(DATA_DIR, f"CodeSearchNet_dataset_{variant}.npy")
    if not os.path.exists(path):
        if variant == "trimmed0":
            orig_path = os.path.join(DATA_DIR, "CodeSearchNet_dataset_original.npy")
            return np.load(orig_path, mmap_mode="r").shape[0]
        print(f"Error: dataset file not found for variant '{variant}': {path}")
        sys.exit(1)
    return np.load(path, mmap_mode="r").shape[0]


def compute_hotness(neighbors, n_vectors):
    """
    hotness[i] = number of queries that include vector i in their top-k list.
    """
    flat = neighbors.reshape(-1)
    hotness = np.bincount(flat, minlength=n_vectors)
    if hotness.size > n_vectors:
        # Shouldn't happen, but trim just in case a stray ID is out of range
        print(f"Warning: {hotness.size - n_vectors} neighbor IDs beyond dataset size")
        hotness = hotness[:n_vectors]
    return hotness


def plot_top_hottest(hotness, variant, collection_name):
    apply_style()
    plt.rcParams.update({
        "axes.titlesize":  19,
        "axes.labelsize":  17,
        "xtick.labelsize": 16,
        "ytick.labelsize": 16,
        "legend.fontsize": 15,
        "legend.title_fontsize": 15,
    })

    top_n = min(TOP_N, hotness.size)
    top_idx = np.argpartition(-hotness, top_n - 1)[:top_n]
    top_idx = top_idx[np.argsort(-hotness[top_idx])]
    top_vals = hotness[top_idx]

    fig, ax = plt.subplots(figsize=(12, 5))
    x = np.arange(top_n)
    ax.bar(x, top_vals, width=1.0, color="#d95f02")

    ax.set_xlabel(f"Rank (top-{top_n} hottest vectors)")
    ax.set_ylabel("# queries containing vector in top-100")
    ax.set_title(f"Top-{top_n} hottest vectors, {collection_name}")
    ax.set_xlim(-0.5, top_n - 0.5)
    y_top = max(1, int(top_vals.max())) * 1.1
    ax.set_ylim(0, y_top)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))

    print(f"\nHottest vectors summary (top {top_n}):")
    print(f"  max hits   : {int(top_vals.max())}")
    print(f"  min hits   : {int(top_vals.min())}")
    print(f"  mean hits  : {top_vals.mean():.2f}")

    plt.tight_layout()
    out_path = f"vector_hotness_top{top_n}_{collection_name}.pdf"
    plt.savefig(out_path)
    print(f"Saved top-hottest plot to {out_path}")
    plt.close(fig)


def plot_hotness_cdf(hotness, variant, collection_name):
    apply_style()
    plt.rcParams.update({
        "axes.titlesize":  19,
        "axes.labelsize":  17,
        "xtick.labelsize": 16,
        "ytick.labelsize": 16,
        "legend.fontsize": 15,
        "legend.title_fontsize": 15,
    })

    n_vectors = hotness.size
    sorted_h = np.sort(hotness)
    cdf = np.arange(1, n_vectors + 1) / n_vectors

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(sorted_h, cdf, color="#1b9e77", linewidth=1.5)

    ax.set_xlabel("Hotness (# queries listing vector in top-100)")
    ax.set_ylabel("CDF over all vectors")
    ax.set_title(f"Hotness CDF, {collection_name}")
    ax.set_ylim(0.8, 1.005)
    ax.set_xlim(left=0)
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.grid(True, alpha=0.3)

    zero_frac = (hotness == 0).sum() / n_vectors
    nonzero = hotness[hotness > 0]
    print(f"\nHotness CDF summary over {n_vectors:,} vectors:")
    print(f"  vectors never hit : {(hotness == 0).sum():,} ({100*zero_frac:.2f}%)")
    if nonzero.size > 0:
        print(f"  among hit vectors : mean={nonzero.mean():.2f}, "
              f"median={int(np.median(nonzero))}, max={int(nonzero.max())}")

    plt.tight_layout()
    out_path = f"vector_hotness_cdf_{collection_name}.pdf"
    plt.savefig(out_path)
    print(f"Saved hotness CDF plot to {out_path}")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(
        description="Plot vector hotness for a CodeSearchNet collection."
    )
    parser.add_argument("collection", help="Name of the Milvus collection")
    args = parser.parse_args()

    variant = collection_to_variant(args.collection)
    neighbors = load_neighbors(variant)
    n_vectors = get_n_vectors(variant)
    print(f"Variant '{variant}' has {n_vectors:,} vectors in the dataset.")

    hotness = compute_hotness(neighbors, n_vectors)
    plot_top_hottest(hotness, variant, args.collection)
    plot_hotness_cdf(hotness, variant, args.collection)


if __name__ == "__main__":
    main()
