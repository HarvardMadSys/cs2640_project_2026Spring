"""
Minimal vLLM single-prompt smoke test.

Imports vLLM as a library (no CLI, no server) and generates one response
from a small model. Used only to confirm that the environment works.

Run:
    python benchmarks/vllm_smoke_single.py
"""

import os
import time

# --- MIG workaround for vLLM 0.7.3 ---
# On a MIG slice, CUDA_VISIBLE_DEVICES is a UUID like "MIG-...", and
# vllm.platforms.cuda tries to int() it. Patch the lookup to return 0
# (torch/pynvml will still see the MIG slice correctly as logical dev 0).
if "MIG-" in os.environ.get("CUDA_VISIBLE_DEVICES", ""):
    import vllm.platforms.cuda as _vcuda
    _vcuda.device_id_to_physical_device_id = lambda device_id=0: 0

from vllm import LLM, SamplingParams

MODEL = "Qwen/Qwen3-0.6B"

PROMPT = "Q: What is the capital of France? Answer in one short sentence.\nA:"


def main():
    t0 = time.time()
    llm = LLM(
        model=MODEL,
        dtype="bfloat16",
        max_model_len=2048,
        gpu_memory_utilization=0.6,
        enforce_eager=True,
    )
    t_load = time.time() - t0
    print(f"[smoke] model loaded in {t_load:.1f}s")

    params = SamplingParams(temperature=0.0, max_tokens=64)
    t0 = time.time()
    outputs = llm.generate([PROMPT], params)
    t_gen = time.time() - t0

    text = outputs[0].outputs[0].text.strip()
    print(f"\n[smoke] prompt : {PROMPT!r}")
    print(f"[smoke] output : {text!r}")
    print(f"[smoke] gen    : {t_gen:.2f}s")
    print("[smoke] OK" if text else "[smoke] EMPTY OUTPUT")


if __name__ == "__main__":
    main()
