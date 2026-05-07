"""HF-side probe of KV reuse on Qwen3.6 hybrid attention.

What this exercises (and what it doesn't)
-----------------------------------------
vLLM is the canonical stage to test prefix-cache + tool-calling
corruption (the omlx#825 / mlx-lm#980 failure mode), but no vLLM
release we can `pip install` here registers Qwen3_5ForConditionalGeneration
(see qwen36_findings.md). Building vLLM from source is out of scope.

We can still get partial signal directly through `transformers`:
  * generate over prompt P_A, capture `past_key_values`
  * generate over prompt P_B that shares the first |P_A| tokens
    BUT pass `past_key_values=A's_pkv` and only feed the new tokens
  * compare the resulting tool-call to a "cold" run of P_B from scratch

If the recurrent state in linear-attn layers is reused correctly,
warm and cold should produce identical outputs. If not, we'll see
divergence — the same failure pattern reported in the upstream issues.

Notes:
* For a full-attention model this probe is uninteresting (HF KV
  reuse is well-tested and lossless).
* For Qwen3.6's hybrid stack this exercises the cross-request reuse
  path of GatedDeltaNet's `Qwen3_5DynamicCache` (or whatever the
  transformers 5.6 implementation calls it). This is exactly the
  state-transfer path that mlx-lm#980 hypothesized was buggy.

Usage (on a node with weights + GPU + transformers 5.6):
    python hf_kv_reuse_probe.py --model Qwen/Qwen3.6-27B-FP8

Output:
  paper/paper2/out/hf_kv_reuse.json
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import torch


SHARED_PREFIX = (
    "You are a careful research assistant. When the user asks for "
    "information that requires up-to-date knowledge, you must call "
    "the web_search tool rather than answering from memory. "
    "Tool definition: web_search(query:str, max_results:int=5) -> list[dict]. "
    "When you call it, emit exactly: <tool_call>"
    '{"name":"web_search","arguments":{"query":"...","max_results":3}}'
    "</tool_call>. Do not emit any other text after the tool call. "
)


SUFFIX_A = "User: What is the current population of Tokyo?\nAssistant:"
SUFFIX_B = "User: What is the current population of Paris?\nAssistant:"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen3.6-27B-FP8")
    ap.add_argument("--max-new", type=int, default=128)
    ap.add_argument("--prefix-repeats", type=int, default=15,
                    help="repeat SHARED_PREFIX this many times to bulk it up")
    ap.add_argument("--out", default="paper/paper2/out/hf_kv_reuse.json")
    args = ap.parse_args()

    os.environ.setdefault("HF_HOME",
                          "/n/netscratch/idreos_lab/Lab/vcainamisir/hf_cache")

    from transformers import (AutoConfig, AutoTokenizer,
                              AutoModelForImageTextToText,
                              AutoModelForCausalLM)
    import transformers
    print("transformers:", transformers.__version__, "torch:", torch.__version__)

    tok = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    cfg = AutoConfig.from_pretrained(args.model, trust_remote_code=True)
    is_vl = "ConditionalGeneration" in (cfg.architectures or [""])[0]

    if is_vl:
        full = AutoModelForImageTextToText.from_pretrained(
            args.model, torch_dtype="auto", device_map="cuda",
            trust_remote_code=True, attn_implementation="sdpa")
        for attr in ("vision_model", "vision_tower", "visual"):
            if hasattr(full, attr):
                delattr(full, attr)
        model = full.eval()
    else:
        model = AutoModelForCausalLM.from_pretrained(
            args.model, torch_dtype="auto", device_map="cuda",
            trust_remote_code=True, attn_implementation="sdpa").eval()

    prefix_text = SHARED_PREFIX * args.prefix_repeats

    pa_text = prefix_text + SUFFIX_A
    pb_text = prefix_text + SUFFIX_B
    pa_ids = tok(pa_text, return_tensors="pt").input_ids.to("cuda")
    pb_ids = tok(pb_text, return_tensors="pt").input_ids.to("cuda")

    # The shared prefix length in tokens (use whichever encodes shorter).
    # Find the longest token-prefix where pa_ids[i] == pb_ids[i].
    L = min(pa_ids.shape[1], pb_ids.shape[1])
    n_shared = 0
    for i in range(L):
        if int(pa_ids[0, i]) == int(pb_ids[0, i]):
            n_shared = i + 1
        else:
            break
    print(f"shared tokens: {n_shared} / pa={pa_ids.shape[1]} pb={pb_ids.shape[1]}")

    # ---- Run A (cold) and capture past_key_values up to position n_shared
    with torch.no_grad():
        # Process the shared prefix, capture KV.
        pre_ids = pa_ids[:, :n_shared]
        out = model(pre_ids, use_cache=True)
        warm_pkv = out.past_key_values
        print("warm_pkv type:", type(warm_pkv).__name__)
        # Keep generating from there to produce A's full output (cold A).
        cold_a = model.generate(
            pa_ids, max_new_tokens=args.max_new, do_sample=False,
            pad_token_id=tok.eos_token_id,
        )

    cold_a_text = tok.decode(cold_a[0, pa_ids.shape[1]:], skip_special_tokens=True)

    # ---- Run B cold (from scratch)
    torch.cuda.synchronize()
    t0 = time.time()
    with torch.no_grad():
        cold_b = model.generate(
            pb_ids, max_new_tokens=args.max_new, do_sample=False,
            pad_token_id=tok.eos_token_id,
        )
    torch.cuda.synchronize()
    cold_b_dt = time.time() - t0
    cold_b_text = tok.decode(cold_b[0, pb_ids.shape[1]:], skip_special_tokens=True)

    # ---- Run B warm (reuse warm_pkv from A's prefix)
    pb_tail_ids = pb_ids[:, n_shared:]
    torch.cuda.synchronize()
    t0 = time.time()
    try:
        with torch.no_grad():
            warm_b = model.generate(
                pb_ids,
                past_key_values=warm_pkv,
                max_new_tokens=args.max_new,
                do_sample=False,
                pad_token_id=tok.eos_token_id,
            )
        warm_b_text = tok.decode(warm_b[0, pb_ids.shape[1]:], skip_special_tokens=True)
        warm_err = None
    except Exception as e:
        warm_b_text = None
        warm_err = f"{type(e).__name__}: {e}"
    torch.cuda.synchronize()
    warm_b_dt = time.time() - t0

    same = (warm_b_text == cold_b_text)
    result = {
        "model": args.model,
        "n_shared_tokens": n_shared,
        "cold_a": {"text": cold_a_text[:1000]},
        "cold_b": {"text": cold_b_text[:1000], "latency_s": cold_b_dt},
        "warm_b": {
            "text": (warm_b_text or "")[:1000],
            "latency_s": warm_b_dt,
            "error": warm_err,
        },
        "warm_equals_cold": same,
        "warm_speedup": cold_b_dt / warm_b_dt if warm_b_dt > 0 else None,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(result, fh, indent=2)
    print("warm_equals_cold:", same)
    print("cold_b_dt:", cold_b_dt, "warm_b_dt:", warm_b_dt)
    if warm_err:
        print("warm_err:", warm_err)
    print("wrote:", args.out)


if __name__ == "__main__":
    main()
