"""Build a partitioned Wikipedia ANN benchmark from Cohere wiki + TriviaQA.

Stages:
  1. Download wiki text chunks from Cohere/wikipedia-2023-11-embed-multilingual-v3
     (en config, streaming). This is the public successor to the retired
     wikipedia-22-12-en-embeddings dataset; passage structure is identical.
  2. Load TriviaQA questions (rc.nocontext, validation split).
  3. Embed both with a public sentence-transformer on GPU.
  4. Brute-force KNN (k=100) on GPU, cosine similarity.
  5. Greedily grow shared_knn until >=50% of queries hit >=80% coverage.
  6. Split queries 50/50 into partition_A / partition_B.
  7. Assign each query's non-shared KNN vectors to its partition.
  8. Scatter all non-shared vectors 80/10/10 into shared/A/B.
  9. Persist embeddings, assignments, and a human-readable summary under wiki/info.

Re-running with --reuse skips any stage whose outputs already exist.

Example (1M wiki passages, 2000 queries, both RTX PRO 6000 cards, skip GPU 1).
--devices drives BOTH embedding (multi-process pool) and KNN (wiki sharded
row-wise, running top-k per shard merged on CPU at the end):
  python prepare_wiki_partitions.py \
      --num-wiki 1000000 --num-queries 2000 \
      --model BAAI/bge-base-en-v1.5 \
      --devices cuda:0,cuda:2 \
      --embed-batch 256 --query-knn-batch 128 \
      --reuse
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import torch
from datasets import load_dataset
from sentence_transformers import SentenceTransformer


HERE = Path(__file__).resolve().parent
INFO_DIR = HERE / "info"

WIKI_TEXTS_PATH = INFO_DIR / "wiki_texts.parquet"
WIKI_EMB_PATH = INFO_DIR / "wiki_embeddings.npy"
QUERIES_PATH = INFO_DIR / "queries.parquet"
QUERY_EMB_PATH = INFO_DIR / "query_embeddings.npy"
KNN_PATH = INFO_DIR / "knn_indices.npy"
KNN_SIMS_PATH = INFO_DIR / "knn_sims.npy"
PARTITIONS_PATH = INFO_DIR / "partitions.parquet"
ASSIGNMENT_PATH = INFO_DIR / "query_assignment.parquet"
SUMMARY_PATH = INFO_DIR / "summary.log"
META_PATH = INFO_DIR / "meta.json"


def setup_logging(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)s %(message)s"
    logger = logging.getLogger("wiki_prep")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fh = logging.FileHandler(log_path, mode="w")
    fh.setFormatter(logging.Formatter(fmt))
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter(fmt))
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def load_wiki_texts(n: int, dataset: str, config: str | None, logger: logging.Logger) -> list[dict]:
    """Stream N text chunks from a HF Wikipedia dataset."""
    if WIKI_TEXTS_PATH.exists():
        tbl = pq.read_table(WIKI_TEXTS_PATH)
        if tbl.num_rows >= n:
            logger.info("Reusing cached wiki texts: %d rows", tbl.num_rows)
            return tbl.slice(0, n).to_pylist()
        logger.info("Cached wiki texts too small (%d < %d), re-downloading", tbl.num_rows, n)

    logger.info("Streaming %d chunks from %s%s", n, dataset, f" ({config})" if config else "")
    if config:
        ds = load_dataset(dataset, config, split="train", streaming=True)
    else:
        ds = load_dataset(dataset, split="train", streaming=True)
    out = []
    t0 = time.time()
    for i, row in enumerate(ds):
        if i >= n:
            break
        wid = row.get("_id") or row.get("id") or i
        try:
            wid_int = int(wid)
        except (ValueError, TypeError):
            wid_int = i
        out.append(
            {
                "vec_id": i,
                "wiki_id": wid_int,
                "title": row.get("title", "") or "",
                "text": row.get("text", "") or "",
                "url": row.get("url", "") or "",
            }
        )
        if (i + 1) % 50000 == 0:
            logger.info("  streamed %d (%.1f/s)", i + 1, (i + 1) / (time.time() - t0))
    logger.info("Streamed %d chunks in %.1fs", len(out), time.time() - t0)

    tbl = pa.Table.from_pylist(out)
    pq.write_table(tbl, WIKI_TEXTS_PATH)
    logger.info("Wrote %s", WIKI_TEXTS_PATH)
    return out


def load_trivia_queries(n: int, logger: logging.Logger) -> list[dict]:
    if QUERIES_PATH.exists():
        tbl = pq.read_table(QUERIES_PATH)
        if tbl.num_rows >= n:
            logger.info("Reusing cached queries: %d rows", tbl.num_rows)
            return tbl.slice(0, n).to_pylist()

    logger.info("Loading %d TriviaQA validation questions", n)
    ds = load_dataset(
        "mandarjoshi/trivia_qa", "rc.nocontext", split="validation", streaming=True
    )
    out = []
    for i, row in enumerate(ds):
        if i >= n:
            break
        out.append(
            {
                "q_idx": i,
                "question_id": row["question_id"],
                "question": row["question"],
                "answer": row["answer"]["value"],
            }
        )
    logger.info("Loaded %d queries", len(out))
    return out


def embed_texts(
    texts: list[str],
    model_name: str,
    batch_size: int,
    devices: list[str],
    is_query: bool,
    logger: logging.Logger,
) -> np.ndarray:
    logger.info(
        "Embedding %d %s with %s (bs=%d, devices=%s)",
        len(texts),
        "queries" if is_query else "passages",
        model_name,
        batch_size,
        devices,
    )
    primary = devices[0]
    model = SentenceTransformer(model_name, device=primary)
    model.eval()

    # BGE v1.5 recommends a query prompt; no prompt for passages.
    prompt = None
    if is_query and "bge" in model_name.lower() and "v1.5" in model_name.lower():
        prompt = "Represent this sentence for searching relevant passages: "

    t0 = time.time()
    if len(devices) > 1 and len(texts) >= 1024:
        logger.info("Using multi-GPU encode across %s", devices)
        pool = model.start_multi_process_pool(target_devices=devices)
        try:
            embs = model.encode_multi_process(
                texts,
                pool=pool,
                batch_size=batch_size,
                prompt=prompt,
                normalize_embeddings=True,
                show_progress_bar=True,
            )
        finally:
            model.stop_multi_process_pool(pool)
    else:
        with torch.inference_mode():
            embs = model.encode(
                texts,
                batch_size=batch_size,
                show_progress_bar=True,
                convert_to_numpy=True,
                normalize_embeddings=True,
                prompt=prompt,
            )
    elapsed = max(time.time() - t0, 1e-6)
    logger.info("Embedded in %.1fs (%.0f/s)", elapsed, len(texts) / elapsed)
    return embs.astype(np.float32, copy=False)


def _knn_shard_worker(
    shard_start: int,
    shard_end: int,
    device: str,
    wiki_emb: np.ndarray,
    query_emb: np.ndarray,
    k: int,
    query_batch: int,
    wiki_chunk: int,
    logger: logging.Logger,
) -> tuple[np.ndarray, np.ndarray]:
    """Top-k against wiki[shard_start:shard_end] on a single device. Thread-safe."""
    Q = query_emb.shape[0]
    torch.cuda.set_device(device) if device.startswith("cuda") else None
    t0 = time.time()

    q_all = torch.from_numpy(query_emb).to(device, non_blocking=True)
    top_sim = torch.full((Q, k), -float("inf"), device=device, dtype=torch.float32)
    top_idx = torch.full((Q, k), -1, device=device, dtype=torch.int64)

    with torch.inference_mode():
        for cstart in range(shard_start, shard_end, wiki_chunk):
            cend = min(cstart + wiki_chunk, shard_end)
            chunk_np = wiki_emb[cstart:cend]
            # Row slice of a C-contiguous 2D array is contiguous.
            chunk = torch.from_numpy(np.ascontiguousarray(chunk_np)).to(device, non_blocking=True)
            chunk_k = min(k, chunk.shape[0])
            for qs in range(0, Q, query_batch):
                qe = min(qs + query_batch, Q)
                sims = q_all[qs:qe] @ chunk.T
                c_sim, c_idx = torch.topk(sims, k=chunk_k, dim=1, largest=True, sorted=True)
                c_idx = c_idx + cstart
                merged_sim = torch.cat([top_sim[qs:qe], c_sim], dim=1)
                merged_idx = torch.cat([top_idx[qs:qe], c_idx], dim=1)
                new_sim, order = torch.topk(merged_sim, k=k, dim=1, largest=True, sorted=True)
                top_sim[qs:qe] = new_sim
                top_idx[qs:qe] = torch.gather(merged_idx, 1, order)
                del sims, c_sim, c_idx, merged_sim, merged_idx, order
            del chunk
            if device.startswith("cuda"):
                torch.cuda.empty_cache()
            logger.info(
                "  [%s] wiki %d/%d (shard %.1f%%, %.1fs)",
                device, cend, shard_end,
                100.0 * (cend - shard_start) / max(shard_end - shard_start, 1),
                time.time() - t0,
            )

    sim_np = top_sim.cpu().numpy()
    idx_np = top_idx.cpu().numpy()
    del q_all, top_sim, top_idx
    if device.startswith("cuda"):
        torch.cuda.empty_cache()
    return sim_np, idx_np


def gpu_knn(
    wiki_emb: np.ndarray,
    query_emb: np.ndarray,
    k: int,
    devices: list[str] | str,
    query_batch: int,
    logger: logging.Logger,
    wiki_chunk: int = 10_000_000,
) -> tuple[np.ndarray, np.ndarray]:
    """Exact cosine-sim KNN on GPU(s), wiki sharded across devices, each shard
    tiled in `wiki_chunk` rows. Vectors assumed L2-normalized."""
    if isinstance(devices, str):
        devices = [devices]
    N, _ = wiki_emb.shape
    Q = query_emb.shape[0]
    logger.info(
        "Running exact KNN: %d queries x %d vectors, k=%d, wiki_chunk=%d, devices=%s",
        Q, N, k, wiki_chunk, devices,
    )
    t0 = time.time()

    # Shard wiki rows evenly across devices
    shard_size = (N + len(devices) - 1) // len(devices)
    shards = []
    for i, dev in enumerate(devices):
        s = i * shard_size
        e = min(s + shard_size, N)
        if s < e:
            shards.append((s, e, dev))

    if len(shards) == 1:
        s, e, dev = shards[0]
        sim_np, idx_np = _knn_shard_worker(
            s, e, dev, wiki_emb, query_emb, k, query_batch, wiki_chunk, logger
        )
        logger.info("KNN done in %.1fs", time.time() - t0)
        return idx_np, sim_np

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=len(shards)) as ex:
        futs = [
            ex.submit(
                _knn_shard_worker, s, e, dev,
                wiki_emb, query_emb, k, query_batch, wiki_chunk, logger,
            )
            for (s, e, dev) in shards
        ]
        results = [f.result() for f in futs]

    # Merge per-shard top-k on CPU: concat along k-axis, then take global top-k.
    sims_cat = np.concatenate([r[0] for r in results], axis=1)  # (Q, S*k)
    idx_cat = np.concatenate([r[1] for r in results], axis=1)
    # argpartition is faster than full sort for big S*k, but k=100 makes sort fine.
    order = np.argsort(-sims_cat, axis=1)[:, :k]
    rows = np.arange(Q)[:, None]
    final_sim = sims_cat[rows, order]
    final_idx = idx_cat[rows, order]
    logger.info("KNN done in %.1fs (merged %d shards)", time.time() - t0, len(shards))
    return final_idx, final_sim


def grow_shared_knn(
    knn: np.ndarray,
    half_cov_target: float,
    query_cov_target: float,
    logger: logging.Logger,
) -> tuple[set[int], dict[int, int]]:
    """Greedy: add most-frequent KNN hits to shared_knn until >=half_cov_target of
    queries have >=query_cov_target of their k-NN captured."""
    Q, k = knn.shape
    counter = Counter()
    for row in knn:
        counter.update(int(x) for x in row)
    logger.info("Distinct target vectors across all KNNs: %d", len(counter))

    # per-query set for fast coverage computation
    per_q = [set(int(x) for x in row) for row in knn]
    # map vec_id -> list of queries that include it
    vec_to_queries: dict[int, list[int]] = {}
    for qi, s in enumerate(per_q):
        for v in s:
            vec_to_queries.setdefault(v, []).append(qi)

    threshold = int(round(query_cov_target * k))  # e.g. 80 hits out of 100
    captured = np.zeros(Q, dtype=np.int32)
    satisfied = np.zeros(Q, dtype=bool)
    needed_satisfied = int(np.ceil(half_cov_target * Q))

    shared: set[int] = set()
    # Sorted most-frequent first (ties broken arbitrarily but stable)
    sorted_vecs = [v for v, _ in counter.most_common()]

    for v in sorted_vecs:
        shared.add(v)
        for qi in vec_to_queries[v]:
            if satisfied[qi]:
                continue
            captured[qi] += 1
            if captured[qi] >= threshold:
                satisfied[qi] = True
        if satisfied.sum() >= needed_satisfied:
            break

    logger.info(
        "shared_knn grown to %d vectors; %d/%d queries have >=%.0f%% coverage",
        len(shared),
        int(satisfied.sum()),
        Q,
        100 * query_cov_target,
    )
    return shared, {int(qi): int(captured[qi]) for qi in range(Q)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--num-wiki", type=int, default=35_000_000)
    ap.add_argument("--num-queries", type=int, default=15000)
    ap.add_argument("--wiki-dataset", default="Cohere/wikipedia-2023-11-embed-multilingual-v3")
    ap.add_argument("--wiki-config", default="en")
    ap.add_argument("--model", default="BAAI/bge-base-en-v1.5")
    ap.add_argument("--k", type=int, default=100)
    ap.add_argument("--embed-batch", type=int, default=512)
    ap.add_argument("--query-knn-batch", type=int, default=128)
    ap.add_argument("--wiki-knn-chunk", type=int, default=10_000_000,
                    help="Wiki rows per GPU tile in the KNN stage. "
                         "10M*768*4B ≈ 30 GB; peak ~40 GB on a 97 GB card.")
    ap.add_argument("--half-coverage", type=float, default=0.5)
    ap.add_argument("--query-coverage", type=float, default=0.8)
    ap.add_argument("--scatter-shared", type=float, default=0.8)
    ap.add_argument("--scatter-a", type=float, default=0.1)
    ap.add_argument("--scatter-b", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--device", default="cuda:0" if torch.cuda.is_available() else "cpu",
                    help="Fallback device if --devices is not set.")
    ap.add_argument("--devices", default=None,
                    help="Comma-separated list of devices for embedding AND KNN "
                         "(e.g. 'cuda:0,cuda:2'). Wiki is sharded row-wise across them "
                         "for the KNN stage. Falls back to --device if unset.")
    ap.add_argument("--reuse", action="store_true", help="Reuse cached embeddings/KNN if present")
    args = ap.parse_args()

    INFO_DIR.mkdir(parents=True, exist_ok=True)
    logger = setup_logging(SUMMARY_PATH)
    logger.info("Args: %s", vars(args))

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    embed_devices = (
        [d.strip() for d in args.devices.split(",") if d.strip()]
        if args.devices
        else [args.device]
    )

    # --- Stage 3: embed (decide if we need to redo it, so stage 1 can skip text load) ---
    wiki_cache_ok = False
    if args.reuse and WIKI_EMB_PATH.exists():
        cached = np.load(WIKI_EMB_PATH, mmap_mode="r")
        if cached.shape[0] >= args.num_wiki:
            wiki_cache_ok = True
            wiki_emb = cached[: args.num_wiki] if cached.shape[0] != args.num_wiki else cached
            logger.info("Reusing cached wiki embeddings (mmap): %s", wiki_emb.shape)
        else:
            logger.info(
                "Cached wiki embeddings too small (%d < %d), re-embedding",
                cached.shape[0], args.num_wiki,
            )
            del cached

    # --- Stage 1: wiki text (skip text materialization when embeddings already cached) ---
    if wiki_cache_ok:
        logger.info("Skipping wiki text materialization (embeddings cached)")
        texts = None
    else:
        wiki_rows = load_wiki_texts(args.num_wiki, args.wiki_dataset, args.wiki_config, logger)
        texts = [r["text"] for r in wiki_rows]
        del wiki_rows

    # --- Stage 2: queries ---
    q_rows = load_trivia_queries(args.num_queries, logger)
    q_texts = [r["question"] for r in q_rows]

    # --- Stage 3 (cont.): embed wiki if not cached ---
    if not wiki_cache_ok:
        wiki_emb = embed_texts(texts, args.model, args.embed_batch, embed_devices, False, logger)
        np.save(WIKI_EMB_PATH, wiki_emb)
        del texts

    if args.reuse and QUERY_EMB_PATH.exists():
        query_emb = np.load(QUERY_EMB_PATH)
        if len(query_emb) != len(q_texts):
            query_emb = embed_texts(q_texts, args.model, args.embed_batch, embed_devices, True, logger)
            np.save(QUERY_EMB_PATH, query_emb)
        else:
            logger.info("Reusing cached query embeddings: %s", query_emb.shape)
    else:
        query_emb = embed_texts(q_texts, args.model, args.embed_batch, embed_devices, True, logger)
        np.save(QUERY_EMB_PATH, query_emb)

    # Persist query parquet (just questions + ids; partition filled later)
    q_tbl = pa.Table.from_pylist(q_rows)
    pq.write_table(q_tbl, QUERIES_PATH)

    # --- Stage 4: KNN (sharded across embed_devices) ---
    def _run_knn():
        return gpu_knn(
            wiki_emb, query_emb, args.k, embed_devices, args.query_knn_batch,
            logger, wiki_chunk=args.wiki_knn_chunk,
        )

    if args.reuse and KNN_PATH.exists():
        knn = np.load(KNN_PATH)
        if knn.shape != (len(q_rows), args.k):
            knn, knn_sims = _run_knn()
            np.save(KNN_PATH, knn)
            np.save(KNN_SIMS_PATH, knn_sims)
        else:
            logger.info("Reusing cached KNN: %s", knn.shape)
    else:
        knn, knn_sims = _run_knn()
        np.save(KNN_PATH, knn)
        np.save(KNN_SIMS_PATH, knn_sims)

    # --- Stage 5: shared_knn ---
    shared_knn, captured_at_stop = grow_shared_knn(
        knn, args.half_coverage, args.query_coverage, logger
    )

    # --- Stage 6: assign queries 50/50 ---
    Q = len(q_rows)
    perm = np.random.permutation(Q)
    half = Q // 2
    a_queries = set(int(i) for i in perm[:half])
    b_queries = set(int(i) for i in perm[half:])
    logger.info("Queries: %d to partition_A, %d to partition_B", len(a_queries), len(b_queries))

    # --- Stage 7: per-query non-shared KNN -> partition A/B ---
    partition_a: set[int] = set()
    partition_b: set[int] = set()
    for qi in range(Q):
        knn_set = set(int(x) for x in knn[qi])
        non_shared = knn_set - shared_knn
        if qi in a_queries:
            partition_a.update(non_shared)
        else:
            partition_b.update(non_shared)

    # --- Stage 8: scatter all non-shared vectors 80/10/10 ---
    N = wiki_emb.shape[0]
    all_ids = np.arange(N, dtype=np.int64)
    non_shared_ids = np.array(sorted(set(int(i) for i in all_ids) - shared_knn), dtype=np.int64)
    rng = np.random.default_rng(args.seed)
    r = rng.random(len(non_shared_ids))
    scatter_to_shared = set(int(i) for i in non_shared_ids[r < args.scatter_shared])
    mask_a = (r >= args.scatter_shared) & (r < args.scatter_shared + args.scatter_a)
    mask_b = r >= args.scatter_shared + args.scatter_a
    scatter_to_a = set(int(i) for i in non_shared_ids[mask_a])
    scatter_to_b = set(int(i) for i in non_shared_ids[mask_b])

    final_shared = shared_knn | scatter_to_shared
    final_a = partition_a | scatter_to_a
    final_b = partition_b | scatter_to_b

    # --- Stage 9: recompute per-query coverage by final_shared (same as shared_knn since scatter only adds non-KNN vectors to shared, but some KNN vecs not in shared_knn remain uncovered) ---
    # For reporting: coverage is capture by the GREEDY shared_knn (the "hot" set).
    coverage_pct = np.zeros(Q, dtype=np.float32)
    for qi in range(Q):
        s = set(int(x) for x in knn[qi])
        coverage_pct[qi] = 100.0 * len(s & shared_knn) / len(s)

    # --- Stage 10: write partition parquet ---
    shared_mask = np.zeros(N, dtype=bool)
    a_mask = np.zeros(N, dtype=bool)
    b_mask = np.zeros(N, dtype=bool)
    for v in final_shared:
        shared_mask[v] = True
    for v in final_a:
        a_mask[v] = True
    for v in final_b:
        b_mask[v] = True

    part_tbl = pa.table(
        {
            "vec_id": all_ids,
            "in_shared": shared_mask,
            "in_A": a_mask,
            "in_B": b_mask,
        }
    )
    pq.write_table(part_tbl, PARTITIONS_PATH)

    # Query assignment parquet
    q_assign = []
    for qi, r_ in enumerate(q_rows):
        q_assign.append(
            {
                "q_idx": qi,
                "question_id": r_["question_id"],
                "question": r_["question"],
                "partition": "A" if qi in a_queries else "B",
                "coverage_pct": float(coverage_pct[qi]),
            }
        )
    pq.write_table(pa.Table.from_pylist(q_assign), ASSIGNMENT_PATH)

    # --- Stage 11: summary ---
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info("Wiki vectors embedded:        %d (dim=%d)", N, wiki_emb.shape[1])
    logger.info("Queries embedded:             %d", Q)
    logger.info("shared_knn (greedy hot set):  %d", len(shared_knn))
    logger.info("final shared_partition size:  %d  (+%d scatter)", len(final_shared), len(scatter_to_shared))
    logger.info("partition_A size:             %d  (knn-derived %d + scatter %d)", len(final_a), len(partition_a), len(scatter_to_a))
    logger.info("partition_B size:             %d  (knn-derived %d + scatter %d)", len(final_b), len(partition_b), len(scatter_to_b))
    logger.info("A ∩ B:                        %d", len(final_a & final_b))
    logger.info("Overlap shared ∩ A:           %d", len(final_shared & final_a))
    logger.info("Overlap shared ∩ B:           %d", len(final_shared & final_b))
    logger.info(
        "Coverage by shared_knn: mean=%.1f%%  median=%.1f%%  >=%.0f%%: %d/%d queries",
        float(coverage_pct.mean()),
        float(np.median(coverage_pct)),
        100 * args.query_coverage,
        int((coverage_pct >= 100 * args.query_coverage).sum()),
        Q,
    )
    logger.info("-" * 60)
    logger.info("Per-query coverage by shared_knn (first 50 shown, full list in assignment parquet):")
    for qi in range(min(50, Q)):
        part = "A" if qi in a_queries else "B"
        logger.info(
            "  q%-4d [%s] cov=%5.1f%%  %s",
            qi,
            part,
            coverage_pct[qi],
            q_rows[qi]["question"][:90],
        )
    logger.info("-" * 60)
    logger.info("partition_A queries (%d): %s", len(a_queries), sorted(a_queries)[:30])
    logger.info("partition_B queries (%d): %s", len(b_queries), sorted(b_queries)[:30])
    logger.info("(...full lists in %s)", ASSIGNMENT_PATH.name)

    # --- Stage 12: meta.json ---
    meta = {
        "model": args.model,
        "dim": int(wiki_emb.shape[1]),
        "num_wiki": N,
        "num_queries": Q,
        "k": args.k,
        "half_coverage": args.half_coverage,
        "query_coverage": args.query_coverage,
        "scatter": {
            "shared": args.scatter_shared,
            "A": args.scatter_a,
            "B": args.scatter_b,
        },
        "shared_knn_size": len(shared_knn),
        "final_shared_size": len(final_shared),
        "partition_A_size": len(final_a),
        "partition_B_size": len(final_b),
        "mean_coverage_pct": float(coverage_pct.mean()),
        "files": {
            "wiki_texts": str(WIKI_TEXTS_PATH.name),
            "wiki_embeddings": str(WIKI_EMB_PATH.name),
            "queries": str(QUERIES_PATH.name),
            "query_embeddings": str(QUERY_EMB_PATH.name),
            "knn_indices": str(KNN_PATH.name),
            "knn_sims": str(KNN_SIMS_PATH.name),
            "partitions": str(PARTITIONS_PATH.name),
            "query_assignment": str(ASSIGNMENT_PATH.name),
        },
    }
    META_PATH.write_text(json.dumps(meta, indent=2))
    logger.info("Wrote meta: %s", META_PATH)
    logger.info("All artifacts under %s", INFO_DIR)


if __name__ == "__main__":
    main()
