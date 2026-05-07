"""
Download the VIBE CodeSearchNet HDF5 from HuggingFace and produce data files
for Milvus benchmarking.

Output files (under data/):
  CodeSearchNet_dataset_original.npy     -- full corpus   (N, 768) float32
  CodeSearchNet_queries_original.npy     -- queries       (Q, 768) float32
  CodeSearchNet_neighbors_original.npy   -- VIBE-provided GT (Q, 100) int32
  CodeSearchNet_distances_original.npy   -- VIBE-provided GT (Q, 100) float32

  CodeSearchNet_neighbors_trimmed0.npy   -- sanity check: recomputed GT with full corpus
  CodeSearchNet_distances_trimmed0.npy   --   (should match *_original if VIBE GT is correct)

  With --skip 0.02:
  CodeSearchNet_dataset_trimmed2.npy     -- corpus with first 2% removed
  CodeSearchNet_queries_trimmed2.npy     -- same queries
  CodeSearchNet_neighbors_trimmed2.npy   -- recomputed GT against trimmed corpus
  CodeSearchNet_distances_trimmed2.npy   -- recomputed GT against trimmed corpus

Usage:
  pip install h5py huggingface_hub numpy tqdm
  python prepare_from_VIBE.py                   # original + trimmed0 sanity check
  python prepare_from_VIBE.py --skip 0.02       # also produce trimmed2 (2% skip)
"""

import argparse
import os
import sys
import numpy as np
import h5py
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DATA_DIR  = os.path.join(os.path.dirname(__file__), "data")
HDF5_URL  = "https://huggingface.co/datasets/vector-index-bench/vibe/resolve/main/codesearchnet-jina-768-cosine.hdf5"
HDF5_NAME = "codesearchnet-jina-768-cosine.hdf5"
TOP_K     = 100

os.makedirs(DATA_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_hdf5() -> str:
    """Download VIBE HDF5 if not already cached, return local path."""
    local_path = os.path.join(DATA_DIR, HDF5_NAME)
    if os.path.exists(local_path):
        print(f"  HDF5 already cached at {local_path}")
        return local_path

    print(f"  Downloading {HDF5_NAME} from HuggingFace …")
    try:
        from huggingface_hub import hf_hub_download
        local_path = hf_hub_download(
            repo_id="vector-index-bench/vibe",
            filename=HDF5_NAME,
            repo_type="dataset",
            local_dir=DATA_DIR,
        )
    except ImportError:
        import urllib.request
        urllib.request.urlretrieve(HDF5_URL, local_path)
    print(f"  Saved to {local_path}")
    return local_path


# ---------------------------------------------------------------------------
# Ground-truth computation
# ---------------------------------------------------------------------------

def compute_ground_truth(
    queries: np.ndarray,
    corpus: np.ndarray,
    top_k: int = TOP_K,
    chunk_size: int = 50_000,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Exact brute-force top-k cosine search.
    Vectors do NOT need to be L2-normalised; we compute true cosine similarity.
    Returns:
      neighbors (Q, K) int32   -- indices into corpus, sorted by similarity descending
      distances (Q, K) float32 -- cosine distance = 1 - cosine_similarity, ascending
    """
    Q = queries.shape[0]
    N = corpus.shape[0]
    neighbors = np.empty((Q, top_k), dtype=np.int32)
    distances = np.empty((Q, top_k), dtype=np.float32)

    # Precompute corpus norms
    corpus_norms = np.linalg.norm(corpus, axis=1)  # (N,)

    for q_start in tqdm(range(0, Q, 100), desc="  computing ground truth"):
        q_end   = min(q_start + 100, Q)
        q_chunk = queries[q_start:q_end]
        q_norms = np.linalg.norm(q_chunk, axis=1, keepdims=True)  # (Qc, 1)

        sims = np.empty((q_end - q_start, N), dtype=np.float32)
        for c_start in range(0, N, chunk_size):
            c_end = min(c_start + chunk_size, N)
            dots = q_chunk @ corpus[c_start:c_end].T
            sims[:, c_start:c_end] = dots / (q_norms * corpus_norms[c_start:c_end])

        top_idx = np.argpartition(-sims, top_k, axis=1)[:, :top_k]
        for i in range(q_end - q_start):
            idx   = top_idx[i]
            s     = sims[i, idx]
            order = np.argsort(-s)
            neighbors[q_start + i] = idx[order].astype(np.int32)
            distances[q_start + i] = (1.0 - s[order]).astype(np.float32)

    return neighbors, distances


# ---------------------------------------------------------------------------
# Save helpers
# ---------------------------------------------------------------------------

def save_set(suffix: str, corpus: np.ndarray, queries: np.ndarray,
             neighbors: np.ndarray, distances: np.ndarray):
    """Save a dataset/query/GT set with the given filename suffix."""
    np.save(os.path.join(DATA_DIR, f"CodeSearchNet_dataset_{suffix}.npy"),   corpus)
    np.save(os.path.join(DATA_DIR, f"CodeSearchNet_queries_{suffix}.npy"),   queries)
    np.save(os.path.join(DATA_DIR, f"CodeSearchNet_neighbors_{suffix}.npy"), neighbors)
    np.save(os.path.join(DATA_DIR, f"CodeSearchNet_distances_{suffix}.npy"), distances)
    print(f"  Saved CodeSearchNet_*_{suffix}.npy: corpus={corpus.shape}, "
          f"queries={queries.shape}, neighbors={neighbors.shape}, "
          f"distances={distances.shape}")


def save_gt(suffix: str, neighbors: np.ndarray, distances: np.ndarray):
    """Save only ground-truth files."""
    np.save(os.path.join(DATA_DIR, f"CodeSearchNet_neighbors_{suffix}.npy"), neighbors)
    np.save(os.path.join(DATA_DIR, f"CodeSearchNet_distances_{suffix}.npy"), distances)
    print(f"  Saved CodeSearchNet_neighbors_{suffix}.npy {neighbors.shape}, "
          f"CodeSearchNet_distances_{suffix}.npy {distances.shape}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Prepare VIBE CodeSearchNet data for Milvus benchmarking."
    )
    parser.add_argument(
        "--skip", type=float, default=0.0,
        help="Fraction of corpus to skip from the front (e.g. 0.02 for 2%%). "
             "Produces trimmed data files with recomputed ground truth."
    )
    args = parser.parse_args()

    # ------------------------------------------------------------------
    # 1. Download / load HDF5
    # ------------------------------------------------------------------
    print("Step 1: Loading VIBE HDF5 …")
    hdf5_path = download_hdf5()
    with h5py.File(hdf5_path, "r") as f:
        corpus    = f["train"][:]       # (N, 768) float32
        queries   = f["test"][:]        # (Q, 768) float32
        neighbors = f["neighbors"][:]   # (Q, 100) int32
        distances = f["distances"][:]   # (Q, 100) float32

    print(f"  corpus    : {corpus.shape}")
    print(f"  queries   : {queries.shape}")
    print(f"  neighbors : {neighbors.shape}")
    print(f"  distances : {distances.shape}")

    if args.skip > 0.0:
        # ------------------------------------------------------------------
        # 2. Produce trimmed set only
        # ------------------------------------------------------------------
        n_skip = int(corpus.shape[0] * args.skip)
        pct = int(round(args.skip * 100))
        suffix = f"trimmed{pct}"
        print(f"Step 2: Trimming first {n_skip} vectors ({args.skip*100:.1f}%) from corpus …")

        trimmed_corpus = corpus[n_skip:]
        print(f"  Trimmed corpus: {trimmed_corpus.shape}")

        print(f"  Recomputing ground truth against trimmed corpus …")
        trimmed_neighbors, trimmed_distances = compute_ground_truth(
            queries, trimmed_corpus, TOP_K,
        )

        save_set(suffix, trimmed_corpus, queries, trimmed_neighbors, trimmed_distances)
    else:
        # ------------------------------------------------------------------
        # 2. Save original files + sanity check
        # ------------------------------------------------------------------
        print("Step 2: Saving original data files …")
        save_set("original", corpus, queries, neighbors, distances)

        print("Step 3: Recomputing GT against full corpus (sanity check) …")
        gt_neighbors_0, gt_distances_0 = compute_ground_truth(queries, corpus, TOP_K)
        save_gt("trimmed0", gt_neighbors_0, gt_distances_0)

        match_rate = np.mean(
            [len(set(a) & set(b)) / TOP_K
             for a, b in zip(neighbors, gt_neighbors_0)]
        )
        print(f"  Sanity check: average overlap with VIBE GT = {match_rate*100:.2f}%")

    print("Done.")


if __name__ == "__main__":
    main()
