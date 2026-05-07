"""Memory-efficient attention extraction.

Monkey-patches `eager_attention_forward` in Qwen3 with a chunked variant
that processes Q in slices of `attn_chunk` positions at a time. For each
chunk we:
  - compute scores = Q_chunk @ K^T / sqrt(D)
  - apply causal mask
  - softmax in fp32
  - if this chunk overlaps the *last `query_window` positions*, accumulate
    per-source-token attention received into a global tensor
  - attn_out[chunk] = softmax_scores @ V
  - free the score tensor before the next chunk

Peak memory per layer: `attn_chunk × N × H × 4 bytes`. At chunk=256, N=8K,
H=16 that's 130 MB — trivially fits on a 20 GB MIG.

The model still produces correct attn_output (numerically identical to
eager attention up to fp ordering), so downstream HF code is unchanged.
We don't need `output_attentions=True` because we capture our stat inside
the patched function itself.

Output:
  studies/lifetime_cost/out/hermes/attention_scores.csv
"""

from __future__ import annotations

import argparse
import csv
import gc
import math
import time
from pathlib import Path
from typing import List

import torch

from studies.lifetime_cost.pipeline.external_traces import load_hermes_agent_traces


# ---------------------------------------------------------------------------
# Chunked attention patch
# ---------------------------------------------------------------------------

class AttnAccumulator:
    """Module-global state to accumulate per-source-token attention received
    from the last `query_window` queries, summed over heads, averaged over
    the deepest `last_k_layers` layers."""

    def __init__(self):
        self.received = None
        self.layer_count = 0
        self.query_window = 256
        self.last_k_layers = 4
        self.attn_chunk = 256
        self._next_layer = 0
        self.kept_layers = set()

    def reset(self, total_layers: int):
        self.received = None
        self.layer_count = 0
        self._next_layer = 0
        self.kept_layers = set(range(max(0, total_layers - self.last_k_layers), total_layers))

    def absorb(self, scores_window):
        """`scores_window` is [B, H, W_actual, N] of softmaxed attention.
        Reduce to [N] and add to `self.received`."""
        # sum over batch, heads, query window dim → [N]
        contrib = scores_window.sum(dim=(0, 1, 2)).float()
        H = scores_window.shape[1]
        contrib = contrib / H
        if self.received is None:
            self.received = torch.zeros(contrib.shape[0], device=contrib.device, dtype=torch.float32)
        # Pad if shapes differ (last layer might have different seq length due to padding/etc)
        if self.received.shape[0] != contrib.shape[0]:
            n = min(self.received.shape[0], contrib.shape[0])
            self.received[:n] += contrib[:n]
        else:
            self.received += contrib

    def finalize(self):
        if self.received is None or self.layer_count == 0:
            return None
        return (self.received / self.layer_count).cpu()


ACC = AttnAccumulator()


def _patched_eager(module, query, key, value, attention_mask, scaling, dropout=0.0, **kwargs):
    """Drop-in replacement for transformers' eager_attention_forward that
    processes Q in chunks. Numerically equivalent (up to fp ordering)."""
    # Q: [B, H_q, N_q, D]
    # K, V: [B, H_kv, N_kv, D]   (H_q = num_attention_heads, H_kv = num_key_value_heads)
    # If GQA, repeat K, V to match H_q heads
    B, H_q, N_q, D = query.shape
    H_kv = key.shape[1]
    if H_kv != H_q:
        repeat = H_q // H_kv
        key = key.repeat_interleave(repeat, dim=1)
        value = value.repeat_interleave(repeat, dim=1)
    N_k = key.shape[2]

    attn_out = torch.empty(B, H_q, N_q, D, dtype=query.dtype, device=query.device)
    layer_idx = ACC._next_layer
    ACC._next_layer += 1
    capture_this_layer = layer_idx in ACC.kept_layers

    chunk = ACC.attn_chunk
    qw = ACC.query_window
    window_start = max(0, N_q - qw)

    for s in range(0, N_q, chunk):
        e = min(s + chunk, N_q)
        # scores: [B, H, e-s, N_k]
        q_chunk = query[:, :, s:e, :]
        scores = torch.matmul(q_chunk, key.transpose(-2, -1)) * scaling

        # Causal mask: positions > query_pos get -inf. We rely on
        # attention_mask if provided, else build the causal slice.
        if attention_mask is not None:
            # attention_mask shape varies; if present, slice it for our chunk
            # Common shape: [B, 1, N_q, N_k] additive mask
            if attention_mask.dim() == 4:
                am = attention_mask[:, :, s:e, :N_k]
                scores = scores + am
        else:
            # build causal: for query at row r=s+i, mask positions > s+i
            row = torch.arange(s, e, device=scores.device).unsqueeze(1)
            col = torch.arange(N_k, device=scores.device).unsqueeze(0)
            mask = (col > row).unsqueeze(0).unsqueeze(0)  # [1, 1, e-s, N_k]
            scores = scores.masked_fill(mask, float("-inf"))

        scores = torch.nn.functional.softmax(scores, dim=-1, dtype=torch.float32).to(query.dtype)

        # Capture stat: chunk overlap with last `qw` query positions
        if capture_this_layer and e > window_start:
            wl = max(s, window_start) - s
            wh = e - s
            ACC.absorb(scores[:, :, wl:wh, :])

        if dropout and dropout > 0:
            scores = torch.nn.functional.dropout(scores, p=dropout, training=False)

        attn_out[:, :, s:e, :] = torch.matmul(scores, value)
        del scores

    if capture_this_layer:
        ACC.layer_count += 1

    # Convert back to [B, N_q, H_q*D] which is what HF returns post-eager
    attn_out = attn_out.transpose(1, 2).contiguous()
    attn_out = attn_out.reshape(B, N_q, H_q * D)
    return attn_out, None


def install_patch():
    import transformers.models.qwen3.modeling_qwen3 as q3
    q3.eager_attention_forward = _patched_eager
    # Also override the attention dispatcher mapping if present
    try:
        from transformers.modeling_utils import ALL_ATTENTION_FUNCTIONS
        ALL_ATTENTION_FUNCTIONS["eager"] = _patched_eager
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Message rendering — same as before
# ---------------------------------------------------------------------------

def _format_for_model(messages, tokenizer, *, drop_system=False, system_truncate=0):
    role_map = {"system": "system", "user": "user", "assistant": "assistant", "tool": "user"}
    out = []
    text = ""
    for m in messages:
        if m["role"] == "system":
            if drop_system:
                continue
            content = (m.get("content") or "")[:system_truncate] if system_truncate > 0 else (m.get("content") or "")
        else:
            content = m.get("content") or ""
        role = role_map.get(m["role"], "user")
        char_start = len(text)
        text += f"<|im_start|>{role}\n{content}<|im_end|>\n"
        char_end = len(text)
        out.append({"role": m["role"], "_msg_id": m.get("_msg_id"),
                    "char_start": char_start, "char_end": char_end})
    return text, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen3-0.6B")
    ap.add_argument("--max-traces", type=int, default=30)
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--last-k-layers", type=int, default=4)
    ap.add_argument("--query-window", type=int, default=256)
    ap.add_argument("--attn-chunk", type=int, default=256)
    ap.add_argument("--out", default="studies/lifetime_cost/out/hermes/attention_scores.csv")
    ap.add_argument("--ref-csv", default="studies/lifetime_cost/out/hermes/reference_graph.csv")
    ap.add_argument("--drop-system", action="store_true")
    ap.add_argument("--system-truncate", type=int, default=512)
    args = ap.parse_args()

    # Install the chunked-attention patch BEFORE loading the model
    install_patch()
    ACC.query_window = args.query_window
    ACC.last_k_layers = args.last_k_layers
    ACC.attn_chunk = args.attn_chunk

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"device={device}, model={args.model}, max_tokens={args.max_tokens}, attn_chunk={args.attn_chunk}, qw={args.query_window}, last_k={args.last_k_layers}")
    if device == "cuda":
        free, total = torch.cuda.mem_get_info(0)
        print(f"  GPU mem: {free/1e9:.1f} GB free / {total/1e9:.1f} GB total — {torch.cuda.get_device_name(0)}")

    from transformers import AutoTokenizer, AutoModelForCausalLM
    t0 = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    tokenizer.truncation_side = "left"
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        torch_dtype=torch.float16,
        attn_implementation="eager",     # our patch is now installed
        trust_remote_code=True,
    ).to(device).eval()
    n_layers = model.config.num_hidden_layers
    print(f"  loaded in {time.perf_counter() - t0:.1f}s, n_layers={n_layers}")

    print(f"\nLoading {args.max_traces} Hermes traces...")
    trajs = load_hermes_agent_traces(config="kimi", max_traces=args.max_traces)
    print(f"  got {len(trajs)} traces")

    # Resume from existing CSV
    rows = []
    already = set()
    if Path(args.out).exists():
        try:
            with open(args.out) as f:
                for r in csv.DictReader(f):
                    already.add(r["task_id"])
                    rows.append(r)
            print(f"  resume: skipping {len(already)} already-done task_ids")
        except Exception:
            pass

    for ti, traj in enumerate(trajs):
        if traj.task_id in already:
            continue
        msgs = traj.extra.get("trajectory_messages") or []
        if not msgs:
            continue
        text, meta = _format_for_model(msgs, tokenizer, drop_system=args.drop_system, system_truncate=args.system_truncate)

        enc = tokenizer(text, return_offsets_mapping=True, add_special_tokens=False,
                        truncation=True, max_length=args.max_tokens)
        input_ids = enc["input_ids"]
        offsets = enc["offset_mapping"]

        # Token ranges per message
        ranges = []
        for m in meta:
            cs, ce = m["char_start"], m["char_end"]
            first = last = None
            for ti2, (a, b) in enumerate(offsets):
                if a == 0 and b == 0:
                    continue
                if first is None and a >= cs:
                    first = ti2
                if a < ce:
                    last = ti2
            if first is None or last is None or last < first:
                ranges.append((0, 0))
            else:
                ranges.append((first, min(last + 1, len(input_ids))))

        if len(input_ids) < 8:
            continue

        ACC.reset(n_layers)
        ids_t = torch.tensor([input_ids], device=device)
        with torch.no_grad():
            _ = model(ids_t, output_attentions=False, use_cache=False)
        attn_per_tok = ACC.finalize()
        if attn_per_tok is None:
            print(f"  warn: no attention captured for {traj.task_id[:8]}")
            del ids_t
            torch.cuda.empty_cache(); gc.collect()
            continue

        for mi, (m, (s, e)) in enumerate(zip(meta, ranges)):
            n = e - s
            if n <= 0:
                continue
            seg_attn = attn_per_tok[s:e].sum().item()
            rows.append({
                "task_id": traj.task_id,
                "msg_index": str(mi),
                "role": m["role"],
                "n_msg_tokens": str(int(n)),
                "attn_total": f"{seg_attn:.6f}",
                "attn_per_token": f"{seg_attn/max(n,1):.6f}",
            })

        del ids_t
        torch.cuda.empty_cache(); gc.collect()

        if (ti + 1) % 3 == 0:
            print(f"  done {ti + 1}/{len(trajs)}  ({time.perf_counter() - t0:.0f}s)")
            # Periodic flush
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            with open(args.out, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["task_id", "msg_index", "role", "n_msg_tokens", "attn_total", "attn_per_token"])
                w.writeheader()
                w.writerows(rows)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["task_id", "msg_index", "role", "n_msg_tokens", "attn_total", "attn_per_token"])
        w.writeheader()
        w.writerows(rows)
    print(f"\nWrote {args.out}  ({len(rows)} rows)")

    # Join with citation + relevance
    if Path(args.ref_csv).exists():
        cit = {}
        with open(args.ref_csv) as f:
            for r in csv.DictReader(f):
                cit[(r["task_id"], int(r["msg_index"]))] = int(r["downstream_cites"])
        rel_path = Path(args.out).with_name("relevance_scores.csv")
        rel = {}
        if rel_path.exists():
            with open(rel_path) as f:
                for r in csv.DictReader(f):
                    if r.get("mean_cos_sim"):
                        rel[(r["task_id"], int(r["msg_index"]))] = float(r["mean_cos_sim"])
        join_path = Path(args.out).with_name("attention_vs_proxies.csv")
        with open(join_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["task_id", "msg_index", "role", "tokens", "cites", "mean_cos_sim", "attn_per_token", "attn_total"])
            for r in rows:
                key = (r["task_id"], int(r["msg_index"]))
                w.writerow([
                    r["task_id"], r["msg_index"], r["role"], r["n_msg_tokens"],
                    cit.get(key, ""),
                    f"{rel.get(key, ''):.4f}" if key in rel else "",
                    r["attn_per_token"], r["attn_total"],
                ])
        print(f"Wrote {join_path}")

        # Quick correlations
        import numpy as np
        from collections import defaultdict
        triples = []
        for r in rows:
            key = (r["task_id"], int(r["msg_index"]))
            if key in cit:
                triples.append((cit[key], rel.get(key, float("nan")), float(r["attn_per_token"])))
        if len(triples) > 10:
            arr = np.array(triples, dtype=float)
            cit_v, rel_v, attn_v = arr[:, 0], arr[:, 1], arr[:, 2]
            mask = ~np.isnan(rel_v)
            print(f"\n=== Correlations across {mask.sum()} messages ===")
            print(f"  attn_per_tok vs citation:    Pearson r = {np.corrcoef(attn_v[mask], cit_v[mask])[0,1]:.3f}")
            print(f"  attn_per_tok vs cos_sim:     Pearson r = {np.corrcoef(attn_v[mask], rel_v[mask])[0,1]:.3f}")
            print(f"  citation     vs cos_sim:     Pearson r = {np.corrcoef(cit_v[mask], rel_v[mask])[0,1]:.3f}")


if __name__ == "__main__":
    main()
