"""
Probe: does vLLM free KV blocks after generate() returns when
enable_prefix_caching=False?

Takes get_kv_snapshot() readings before, immediately after, 1s after, and
during a simulated tool window. If freeing happens, used_blocks should
drop to 0 immediately after generate() returns and stay there.
"""

import time
from agent.vllm_engine import InstrumentedEngine

engine = InstrumentedEngine(
    model="Qwen/Qwen2.5-3B-Instruct",
    max_model_len=8192,
    gpu_memory_utilization=0.7,
    enable_prefix_caching=False,
)

def snap(label):
    s = engine.get_kv_snapshot()
    print(f"  {label:35s}  used_blocks={s['used_gpu_blocks']:>6}  "
          f"kv_tokens_used={s['kv_tokens_used']:>6}  "
          f"free={s['free_gpu_blocks']:>6}/{s['total_gpu_blocks']}  "
          f"src={s['source']}")

messages = [
    {"role": "system", "content": "You are a research assistant."},
    {"role": "user",   "content": "Write a 200-word essay about the Transformer architecture."},
]

print("Probe: vLLM block freeing with enable_prefix_caching=False")
print()

snap("[t=0]    BEFORE generate()")
t0 = time.time()
text, _, _, usage = engine.generate_turn(messages, tools=[], max_tokens=300)
t_gen = time.time() - t0
snap(f"[t={t_gen:.2f}s] IMMEDIATELY AFTER generate()")

print(f"  (generated {usage.completion_tokens} tokens, prompt={usage.prompt_tokens})")

# Simulate the tool-call idle window
for delay in [0.5, 1.0, 2.0]:
    time.sleep(delay - (time.time() - t0 - t_gen))
    snap(f"[t={time.time()-t0:.2f}s] during simulated tool window")

# Now run a SECOND request and check before-state
print()
print("--- second request ---")
snap("[before 2nd generate()]")
text, _, _, usage = engine.generate_turn(messages + [
    {"role": "assistant", "content": text},
    {"role": "user", "content": "Now summarize that in one sentence."},
], tools=[], max_tokens=100)
snap("[after 2nd generate()]")
