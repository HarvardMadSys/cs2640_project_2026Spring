"""Qwen3.6-27B-FP8 smoke load + generate.

Goal: confirm we can load the hybrid (linear-attn + full-attn) model in
text-only mode on one 80GB A100 and run model.generate() on a short
prompt and a 32K-token prompt. Logs peak VRAM and tokens/sec.

This is intentionally minimal and does NOT exercise the AdaptiveCache
pipeline. It's the first-line check that the architecture loads at all
in the configured environment.

Required env:
  HF_HOME=/n/netscratch/idreos_lab/Lab/vcainamisir/hf_cache
  Python with transformers >= 5.6.0 (the qwen3_5 architecture).
  4.57.x will fail with KeyError: 'qwen3_5'.
"""

from __future__ import annotations

import argparse
import os
import time

import torch


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="Qwen/Qwen3.6-27B-FP8")
    parser.add_argument("--dtype", default="auto",
                        choices=("auto", "float16", "bfloat16"))
    parser.add_argument("--max-new", type=int, default=64)
    parser.add_argument("--long-ctx", type=int, default=32_000,
                        help="prefill length for the long-context probe")
    parser.add_argument("--skip-long", action="store_true")
    parser.add_argument("--attn", default="sdpa",
                        help="attn_implementation; flash_attention_2 if available")
    args = parser.parse_args()

    os.environ.setdefault("HF_HOME",
                          "/n/netscratch/idreos_lab/Lab/vcainamisir/hf_cache")

    print(f"[{time.strftime('%H:%M:%S')}] importing transformers…")
    from transformers import (
        AutoConfig,
        AutoTokenizer,
        AutoModelForImageTextToText,
        AutoModelForCausalLM,
    )
    import transformers
    print(f"  transformers={transformers.__version__}, torch={torch.__version__}")

    tok = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    cfg = AutoConfig.from_pretrained(args.model, trust_remote_code=True)
    arch0 = (cfg.architectures or [""])[0]
    print(f"  architecture={arch0}")
    is_vl = "ConditionalGeneration" in arch0 or getattr(cfg, "vision_config", None) is not None
    print(f"  is_vl={is_vl}")

    dtype_map = {
        "auto": "auto",
        "float16": torch.float16,
        "bfloat16": torch.bfloat16,
    }
    dtype = dtype_map[args.dtype]

    t0 = time.time()
    if is_vl:
        full = AutoModelForImageTextToText.from_pretrained(
            args.model,
            torch_dtype=dtype,
            attn_implementation=args.attn,
            device_map="cuda",
            trust_remote_code=True,
        )
        # Try to drop the vision tower (it never participates in text-only).
        for attr in ("vision_model", "vision_tower", "visual"):
            if hasattr(full, attr):
                delattr(full, attr)
                print(f"  dropped {attr}")
        model = full.eval()
    else:
        model = AutoModelForCausalLM.from_pretrained(
            args.model,
            torch_dtype=dtype,
            attn_implementation=args.attn,
            device_map="cuda",
            trust_remote_code=True,
        ).eval()
    load_s = time.time() - t0
    print(f"[{time.strftime('%H:%M:%S')}] loaded in {load_s:.1f}s")
    torch.cuda.synchronize()
    idle_mem = torch.cuda.max_memory_allocated() / 1e9
    print(f"  vram_idle_GB={idle_mem:.2f}")

    # ---------- short prompt ----------
    short = "Hi"
    ids = tok(short, return_tensors="pt").input_ids.to("cuda")
    torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    with torch.no_grad():
        out = model.generate(ids, max_new_tokens=args.max_new, do_sample=False,
                             pad_token_id=tok.eos_token_id)
    torch.cuda.synchronize()
    short_s = time.time() - t0
    short_new = int(out.shape[1] - ids.shape[1])
    short_mem = torch.cuda.max_memory_allocated() / 1e9
    short_text = tok.decode(out[0, ids.shape[1]:], skip_special_tokens=True)
    print(f"  short: prefill={int(ids.shape[1])} new={short_new} "
          f"tps={short_new / short_s:.1f} vram_peak_GB={short_mem:.2f}")
    print(f"  short out: {short_text[:120]!r}")

    if args.skip_long:
        return

    # ---------- long-context probe ----------
    # Build a long prompt by repeating natural English text. Avoid token-id
    # tricks: real tokens exercise the full code path.
    seed = ("The hybrid attention architecture interleaves "
            "GatedDeltaNet linear attention with periodic full attention "
            "to reduce KV-cache footprint. ")
    big = (seed * 5000)
    big_ids = tok(big, return_tensors="pt").input_ids[:, :args.long_ctx].to("cuda")
    torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    with torch.no_grad():
        out = model.generate(big_ids, max_new_tokens=args.max_new,
                             do_sample=False, pad_token_id=tok.eos_token_id)
    torch.cuda.synchronize()
    long_s = time.time() - t0
    long_new = int(out.shape[1] - big_ids.shape[1])
    long_mem = torch.cuda.max_memory_allocated() / 1e9
    print(f"  long({int(big_ids.shape[1])}): new={long_new} "
          f"tps={long_new / long_s:.1f} vram_peak_GB={long_mem:.2f}")


if __name__ == "__main__":
    main()
