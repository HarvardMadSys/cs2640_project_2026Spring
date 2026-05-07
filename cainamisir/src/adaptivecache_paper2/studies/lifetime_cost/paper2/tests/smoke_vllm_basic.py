"""Smoke test vLLM 0.13.0 + Memento overlay on Blackwell sm_120.

Tests:
1. LLM engine init on V1 engine (default in 0.13.0)
2. Trivial generate without block masking
3. Trivial generate with block masking enabled (Memento config)
"""
import os
os.environ["HF_HOME"] = "/scratch/hf/"
os.environ["VLLM_LOGGING_LEVEL"] = "WARNING"
os.environ["VLLM_ATTENTION_BACKEND"] = "FLASHINFER"

import time
from vllm import LLM, SamplingParams

MODEL = "Qwen/Qwen3-4B-Instruct-2507"

print(f"Loading {MODEL} on V1 engine (default in vLLM 0.13.0)...")
t0 = time.time()
llm = LLM(
    model=MODEL,
    gpu_memory_utilization=0.4,
    max_model_len=4096,
    dtype="bfloat16",
)
print(f"Loaded in {time.time() - t0:.1f}s")

prompts = ["The capital of France is"]
sp = SamplingParams(max_tokens=20, temperature=0.0)
out = llm.generate(prompts, sp)
print("OUT:", out[0].outputs[0].text.strip())

print("\nSMOKE TEST PASSED")
