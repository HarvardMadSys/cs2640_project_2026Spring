"""
Generate CodeSearchNet embeddings using Jina, matching VIBE benchmark format.

VIBE spec (codesearchnet-jina-768-cosine):
  - Model:      jina-embeddings-v2-base-code (dim=768)
  - Corpus:     ~1,373,067 vectors  (all rows minus 1000 sampled queries)
  - Queries:    1,000 vectors       (sampled with fixed seed)
  - Metric:     cosine (vectors L2-normalised so dot == cosine)
  - GroundTruth: top-100 neighbours per query

Output files (under OUTPUT_DIR):
  codesearchnet-jina-768-cosine.hdf5   -- HDF5 identical to VIBE layout
  dataset.npy                          -- corpus embeddings  (N, 768) float32
  queries.npy                          -- query  embeddings  (Q, 768) float32
  neighbors.npy                        -- ground-truth ids   (Q, 100) int32
  distances.npy                        -- ground-truth dists (Q, 100) float32

Install before running (in pyenv):
  pip install datasets h5py tqdm transformers torch
  pip install transformers==4.44.2
"""

import os
import sys
import numpy as np
import h5py
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
OUTPUT_DIR   = os.path.join(os.path.dirname(__file__), "data")
HDF5_NAME    = "codesearchnet-jina-768-cosine.hdf5"
JINA_MODEL   = "jina-embeddings-v2-base-code"
DIM          = 768
TOP_K        = 100
NUM_QUERIES  = 1000
RANDOM_SEED  = 42
BATCH_SIZE   = 512

EMBED_BACKEND = os.environ.get("EMBED_BACKEND", "local")
JINA_API_KEY  = os.environ.get("JINA_API_KEY", "")

os.makedirs(OUTPUT_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------

def _embed_api(texts: list[str]) -> np.ndarray:
    """Call Jina HTTP API, return (N, 768) float32, L2-normalised."""
    import requests, json
    if not JINA_API_KEY:
        raise RuntimeError("Set JINA_API_KEY env var to use the API backend.")
    url = "https://api.jina.ai/v1/embeddings"
    headers = {
        "Authorization": f"Bearer {JINA_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"model": JINA_MODEL, "input": texts}
    resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=120)
    resp.raise_for_status()
    data = resp.json()["data"]
    vecs = np.array([d["embedding"] for d in data], dtype=np.float32)
    return _l2_normalize(vecs)


def _l2_normalize(vecs: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return vecs / norms


def embed_texts(texts: list[str], model=None, tokenizer=None, device=None) -> np.ndarray:
    """Embed a list of strings in BATCH_SIZE chunks."""
    all_vecs = []
    for start in tqdm(range(0, len(texts), BATCH_SIZE), desc="  embedding batches", leave=False):
        batch = texts[start : start + BATCH_SIZE]
        if EMBED_BACKEND == "api":
            vecs = _embed_api(batch)
        else:
            import torch
            enc = tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            ).to(device)
            with torch.no_grad():
                out = model(**enc)
            mask = enc["attention_mask"].unsqueeze(-1).float()
            vecs = (out.last_hidden_state * mask).sum(1) / mask.sum(1)
            vecs = _l2_normalize(vecs.cpu().float().numpy())
        all_vecs.append(vecs)
    return np.vstack(all_vecs)


# ---------------------------------------------------------------------------
# Ground-truth computation (exact brute-force cosine via dot product)
# L2-normalised vectors: cosine_sim = dot; cosine_dist = 1 - dot
# ---------------------------------------------------------------------------

def compute_ground_truth(
    queries: np.ndarray,
    corpus: np.ndarray,
    top_k: int = TOP_K,
    chunk_size: int = 50_000,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Exact top-k cosine search.
    Returns (neighbors int32 (Q, K), distances float32 (Q, K)) where
    distances are cosine distances (1 - cosine_similarity), ascending order.
    """
    Q = queries.shape[0]
    N = corpus.shape[0]
    neighbors  = np.empty((Q, top_k), dtype=np.int32)
    distances  = np.empty((Q, top_k), dtype=np.float32)

    for q_start in tqdm(range(0, Q, 100), desc="  computing ground truth"):
        q_end   = min(q_start + 100, Q)
        q_chunk = queries[q_start:q_end]          # (Qc, D)

        # Compute cosine similarity in corpus chunks to limit memory
        sims = np.empty((q_end - q_start, N), dtype=np.float32)
        for c_start in range(0, N, chunk_size):
            c_end = min(c_start + chunk_size, N)
            sims[:, c_start:c_end] = q_chunk @ corpus[c_start:c_end].T

        # top-k by similarity (largest first), convert to distance
        top_idx = np.argpartition(-sims, top_k, axis=1)[:, :top_k]
        for i in range(q_end - q_start):
            idx = top_idx[i]
            s   = sims[i, idx]
            order = np.argsort(-s)
            neighbors[q_start + i]  = idx[order].astype(np.int32)
            distances[q_start + i]  = (1.0 - s[order]).astype(np.float32)

    return neighbors, distances


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # ------------------------------------------------------------------
    # 1. Load CodeSearchNet from HuggingFace
    # ------------------------------------------------------------------
    print("Loading CodeSearchNet dataset …")
    from datasets import load_dataset, concatenate_datasets

    languages = ["python", "java", "javascript", "php", "ruby", "go"]
    all_splits = []
    for lang in languages:
        ds = load_dataset("code_search_net", lang, split="train+validation+test")
        all_splits.append(ds)

    full_ds = concatenate_datasets(all_splits)
    print(f"  Total rows: {len(full_ds)}")

    print("  Extracting text column …")
    texts = full_ds.data.column("whole_func_string").to_pylist()

    # ------------------------------------------------------------------
    # 2. Sample query indices (fixed seed, matching VIBE)
    # ------------------------------------------------------------------
    rng = np.random.default_rng(RANDOM_SEED)
    all_indices   = np.arange(len(texts))
    query_indices = rng.choice(all_indices, size=NUM_QUERIES, replace=False)
    query_indices = np.sort(query_indices)

    corpus_mask     = np.ones(len(texts), dtype=bool)
    corpus_mask[query_indices] = False
    corpus_indices  = np.where(corpus_mask)[0]

    query_texts  = [texts[i] for i in query_indices]
    corpus_texts = [texts[i] for i in corpus_indices]
    print(f"  Corpus size : {len(corpus_texts)}")
    print(f"  Query  size : {len(query_texts)}")

    # ------------------------------------------------------------------
    # 3. Set up embedding model
    # ------------------------------------------------------------------
    model = tokenizer = device = None
    if EMBED_BACKEND == "local":
        import torch
        from transformers import AutoModel, AutoTokenizer
        if not torch.cuda.is_available():
            raise RuntimeError("No CUDA GPU found. Cannot use local backend.")
        device = "cuda:0"
        print(f"Loading local Jina model (jinaai/{JINA_MODEL}) on {device} …")
        tokenizer = AutoTokenizer.from_pretrained(
            f"jinaai/{JINA_MODEL}", trust_remote_code=True
        )
        model = AutoModel.from_pretrained(
            f"jinaai/{JINA_MODEL}", trust_remote_code=True
        ).to(device).eval()
        print(f"  Model ready on {device}.")
    else:
        print(f"Using Jina API backend (model={JINA_MODEL})")

    # ------------------------------------------------------------------
    # 4. Embed corpus
    # ------------------------------------------------------------------
    corpus_path = os.path.join(OUTPUT_DIR, "dataset.npy")
    if os.path.exists(corpus_path):
        print("Loading cached corpus embeddings …")
        corpus_vecs = np.load(corpus_path)
    else:
        print("Embedding corpus …")
        corpus_vecs = embed_texts(corpus_texts, model, tokenizer, device)
        np.save(corpus_path, corpus_vecs)
        print(f"  Saved {corpus_path}")

    # ------------------------------------------------------------------
    # 5. Embed queries
    # ------------------------------------------------------------------
    query_path = os.path.join(OUTPUT_DIR, "queries.npy")
    if os.path.exists(query_path):
        print("Loading cached query embeddings …")
        query_vecs = np.load(query_path)
    else:
        print("Embedding queries …")
        query_vecs = embed_texts(query_texts, model, tokenizer, device)
        np.save(query_path, query_vecs)
        print(f"  Saved {query_path}")

    # ------------------------------------------------------------------
    # 6. Compute ground truth
    # ------------------------------------------------------------------
    nbrs_path = os.path.join(OUTPUT_DIR, "neighbors.npy")
    dist_path = os.path.join(OUTPUT_DIR, "distances.npy")
    if os.path.exists(nbrs_path) and os.path.exists(dist_path):
        print("Loading cached ground truth …")
        neighbors = np.load(nbrs_path)
        distances = np.load(dist_path)
    else:
        print("Computing exact top-100 ground truth (brute-force cosine) …")
        neighbors, distances = compute_ground_truth(query_vecs, corpus_vecs, TOP_K)
        np.save(nbrs_path, neighbors)
        np.save(dist_path, distances)
        print(f"  Saved {nbrs_path}, {dist_path}")

    # ------------------------------------------------------------------
    # 7. Write HDF5 (VIBE layout)
    # ------------------------------------------------------------------
    hdf5_path = os.path.join(OUTPUT_DIR, HDF5_NAME)
    print(f"Writing {hdf5_path} …")
    with h5py.File(hdf5_path, "w") as f:
        f.attrs["dimension"] = DIM
        f.attrs["distance"]  = "cosine"
        f.attrs["point_type"] = "float"
        f.create_dataset("train",     data=corpus_vecs, compression="gzip", chunks=True)
        f.create_dataset("test",      data=query_vecs,  compression="gzip", chunks=True)
        f.create_dataset("neighbors", data=neighbors,   compression="gzip", chunks=True)
        f.create_dataset("distances", data=distances,   compression="gzip", chunks=True)
    print("Done.")
    print(f"  train     : {corpus_vecs.shape}")
    print(f"  test      : {query_vecs.shape}")
    print(f"  neighbors : {neighbors.shape}")
    print(f"  distances : {distances.shape}")


if __name__ == "__main__":
    main()
