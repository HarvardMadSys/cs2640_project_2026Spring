"""Backend-agnostic relevance extraction via sentence-embedding similarity.

For each message in a Hermes trajectory, compute its semantic similarity
to the *subsequent assistant turns*. High mean similarity = the message
shaped subsequent reasoning (even with vocabulary that doesn't textually
recur — which is what the citation-based metric was missing).

This is a vLLM-friendly proxy: no eager attention required, no GPU
necessary, runs on top of any inference backend.

We use `all-MiniLM-L6-v2` (~80 MB, 384-dim embeddings, decent quality on
short text). For each (message, future_assistant_turns) pair:
  rel(m_i) = mean_j>i ( cos_sim(emb(m_i), emb(asst_j)) )

Outputs:
  studies/lifetime_cost/out/hermes/relevance_scores.csv
    task_id, msg_index, role, tokens, mean_cos_sim, max_cos_sim, n_future_asst
  studies/lifetime_cost/out/hermes/relevance_vs_citation.csv
    join with reference_graph.csv to compare the two signals
"""

from __future__ import annotations

import argparse
import csv
import re
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import List

import numpy as np

from studies.lifetime_cost.pipeline.external_traces import load_hermes_agent_traces


# Strip Hermes <think>/<tool_call>/<tool_response> wrappers for cleaner embeddings
TAG_RE = re.compile(r"</?(think|tool_call|tool_response)>", re.IGNORECASE)


def _clean(text: str, max_chars: int = 2000) -> str:
    if not text:
        return ""
    t = TAG_RE.sub(" ", text)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:max_chars]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="kimi", choices=["kimi", "glm-5.1"])
    ap.add_argument("--max-traces", type=int, default=100)
    ap.add_argument("--model", default="sentence-transformers/all-MiniLM-L6-v2")
    ap.add_argument("--out", default="studies/lifetime_cost/out/hermes/relevance_scores.csv")
    ap.add_argument("--ref-csv", default="studies/lifetime_cost/out/hermes/reference_graph.csv")
    ap.add_argument("--include-system", action="store_true",
                    help="By default we skip the (large, mostly tool-spec) system message")
    args = ap.parse_args()

    print(f"Loading sentence model {args.model}...")
    from sentence_transformers import SentenceTransformer
    t0 = time.perf_counter()
    enc = SentenceTransformer(args.model)
    print(f"  loaded in {time.perf_counter() - t0:.1f}s, dim={enc.get_sentence_embedding_dimension()}")

    print(f"\nLoading {args.max_traces} Hermes traces from {args.config}...")
    trajs = load_hermes_agent_traces(config=args.config, max_traces=args.max_traces)
    print(f"  got {len(trajs)} traces")

    rows = []
    for ti, traj in enumerate(trajs):
        msgs = traj.extra.get("trajectory_messages") or []
        if not msgs:
            continue

        # Build cleaned text per message
        texts = []
        msg_meta = []
        for i, m in enumerate(msgs):
            if not args.include_system and m["role"] == "system":
                continue
            texts.append(_clean(m.get("content") or ""))
            msg_meta.append({"orig_idx": i, "role": m["role"], "tokens": m.get("_token_count", 0)})

        if len(texts) < 2:
            continue

        # Encode all messages in one batch
        embs = enc.encode(texts, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False)
        # embs: [n_msgs, dim], normalized, so cos_sim = dot product

        # Indices of assistant messages within the cleaned list
        asst_idxs = [j for j, m in enumerate(msg_meta) if m["role"] == "assistant"]

        for j, m in enumerate(msg_meta):
            future_asst = [k for k in asst_idxs if k > j]
            if not future_asst:
                rows.append({
                    "task_id": traj.task_id,
                    "msg_index": m["orig_idx"],
                    "role": m["role"],
                    "tokens": m["tokens"],
                    "mean_cos_sim": "",
                    "max_cos_sim": "",
                    "n_future_asst": 0,
                })
                continue
            sims = embs[future_asst] @ embs[j]      # [n_future]
            rows.append({
                "task_id": traj.task_id,
                "msg_index": m["orig_idx"],
                "role": m["role"],
                "tokens": m["tokens"],
                "mean_cos_sim": float(sims.mean()),
                "max_cos_sim": float(sims.max()),
                "n_future_asst": len(future_asst),
            })

        if (ti + 1) % 10 == 0:
            print(f"  done {ti + 1}/{len(trajs)}  ({time.perf_counter() - t0:.0f}s elapsed)")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["task_id", "msg_index", "role", "tokens",
                                          "mean_cos_sim", "max_cos_sim", "n_future_asst"])
        w.writeheader()
        w.writerows(rows)
    print(f"\nWrote {args.out}  ({len(rows)} rows)")

    # Aggregate: mean cosine similarity by role
    print("\n=== Mean cosine similarity to future assistant turns, by role ===")
    by_role = defaultdict(list)
    for r in rows:
        if r["mean_cos_sim"] != "":
            by_role[r["role"]].append(float(r["mean_cos_sim"]))
    for role, vals in sorted(by_role.items()):
        if vals:
            print(f"  {role:10s}  n={len(vals):4d}  mean={statistics.mean(vals):.3f}  median={statistics.median(vals):.3f}  p25={sorted(vals)[len(vals)//4]:.3f}  p75={sorted(vals)[3*len(vals)//4]:.3f}")

    # Join with citation data
    if Path(args.ref_csv).exists():
        cit = {}
        with open(args.ref_csv) as f:
            for r in csv.DictReader(f):
                cit[(r["task_id"], int(r["msg_index"]))] = int(r["downstream_cites"])
        join_path = Path(args.out).with_name("relevance_vs_citation.csv")
        with open(join_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["task_id", "msg_index", "role", "tokens", "cites", "mean_cos_sim", "max_cos_sim"])
            for r in rows:
                key = (r["task_id"], r["msg_index"])
                if r["mean_cos_sim"] == "":
                    continue
                w.writerow([r["task_id"], r["msg_index"], r["role"], r["tokens"],
                            cit.get(key, ""),
                            f"{r['mean_cos_sim']:.4f}",
                            f"{r['max_cos_sim']:.4f}"])
        print(f"\nWrote {join_path}")

        # Correlation
        pairs = []
        for r in rows:
            key = (r["task_id"], r["msg_index"])
            if key in cit and r["mean_cos_sim"] != "":
                pairs.append((cit[key], float(r["mean_cos_sim"])))
        if len(pairs) > 10:
            xs = np.array([p[0] for p in pairs], dtype=float)
            ys = np.array([p[1] for p in pairs], dtype=float)
            corr = np.corrcoef(xs, ys)[0, 1]
            print(f"\n=== Pearson correlation (citations vs mean_cos_sim): r = {corr:.3f}  (n={len(pairs)}) ===")
            # Spearman rank
            xr = xs.argsort().argsort()
            yr = ys.argsort().argsort()
            spear = np.corrcoef(xr, yr)[0, 1]
            print(f"=== Spearman rank correlation: r = {spear:.3f} ===")


if __name__ == "__main__":
    main()
