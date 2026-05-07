"""Memento masking validation — does block masking actually fire?

Setup: Qwen3-4B-Instruct-2507 with Memento BlockMaskingConfig.
Block tokens chosen from existing Qwen3 vocab (no tokenizer modification):
  block_start    = <tool_response>  (151665)
  block_end      = </tool_response> (151666)
  summary_start  = <|fim_prefix|>   (151659)  [repurposed - not used in chat]
  summary_end    = <|fim_middle|>   (151660)  [repurposed - not used in chat]

We construct a prompt that contains a tool-response block followed by a
summary block, and ask the model to continue. With masking enabled +
debug=True, the engine should log compaction events.
"""
import os
os.environ["HF_HOME"] = "/scratch/hf/"
os.environ["VLLM_LOGGING_LEVEL"] = "INFO"
os.environ["VLLM_ATTENTION_BACKEND"] = "FLASHINFER"

import time
from vllm import LLM, SamplingParams
from vllm.config.block_masking import BlockMaskingConfig

MODEL = "Qwen/Qwen3-4B-Instruct-2507"

bm_config = BlockMaskingConfig(
    enable=True,
    keep_last_n_blocks=0,
    block_start_token="151665",   # <tool_response>
    block_end_token="151666",     # </tool_response>
    summary_start_token="151659", # <|fim_prefix|>
    summary_end_token="151660",   # <|fim_middle|>
    require_assistant_section=False,
    mask_delimiters=False,
    compact_on_summary_end=True,
    restart_mode=True,
    debug=True,
)
print(f"BlockMaskingConfig: enable={bm_config.enable}, debug={bm_config.debug}")

print(f"Loading {MODEL} with Memento masking...")
t0 = time.time()
llm = LLM(
    model=MODEL,
    gpu_memory_utilization=0.4,
    max_model_len=4096,
    dtype="bfloat16",
    block_masking_config=bm_config,
)
print(f"Loaded in {time.time() - t0:.1f}s")

# A prompt with a tool_response block we expect to be masked.
# block_start (151665), block_end (151666) wrap the obs; summary tokens
# (151659, 151660) wrap a memento.
prompt = (
    "<|im_start|>user\n"
    "Look at file foo.py and tell me what it does.<|im_end|>\n"
    "<|im_start|>assistant\n"
    "<tool_call>{\"name\":\"read_file\",\"args\":{\"path\":\"foo.py\"}}</tool_call><|im_end|>\n"
    "<|im_start|>user\n"
    "<tool_response>"
    + ("def add(a, b):\n    return a + b\n\n" * 50)  # ~600 tokens of "obs"
    + "</tool_response>"
    "<|fim_prefix|>"
    "foo.py defines a single function `add(a, b)` that returns a+b."
    "<|fim_middle|>"
    "<|im_end|>\n"
    "<|im_start|>assistant\n"
)

sp = SamplingParams(max_tokens=80, temperature=0.0)
print("\n=== generating ===")
out = llm.generate([prompt], sp)
print("OUT:", repr(out[0].outputs[0].text[:300]))
print("\nMEMENTO SMOKE TEST PASSED")
