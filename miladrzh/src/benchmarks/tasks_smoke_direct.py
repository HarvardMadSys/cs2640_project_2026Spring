"""
Direct check: for each agent type, ask the model once with the real
system prompt + task prompt and print raw text + parsed tool calls.
Confirms whether the small model can emit tool calls.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

if "MIG-" in os.environ.get("CUDA_VISIBLE_DEVICES", ""):
    import vllm.platforms.cuda as _vcuda
    _vcuda.device_id_to_physical_device_id = lambda device_id=0: 0

from agent.vllm_engine import InstrumentedEngine
from agent.loop import SYSTEM_PROMPTS, TOOL_SCHEMAS
from tasks.data_analysis import TASKS_BY_ID as DA
from tasks.sql import TASKS as SQL_T
from tasks.rag import TASKS as RAG_T

MODEL = "Qwen/Qwen2.5-3B-Instruct"


def probe(engine, label, task):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPTS[task["agent_type"]]},
        {"role": "user",   "content": task["prompt"]},
    ]
    tools = TOOL_SCHEMAS[task["agent_type"]]
    text, tool_calls, finish_reason, usage = engine.generate_turn(messages, tools, max_tokens=2048)
    print(f"\n==== {label}  ({task['id']}) ====")
    print(f"finish_reason={finish_reason}  tool_calls={len(tool_calls) if tool_calls else 0}  "
          f"prompt_tokens={usage.prompt_tokens}  completion_tokens={usage.completion_tokens}")
    if tool_calls:
        for tc in tool_calls:
            print(f"  tool_call: {tc['name']}  args={str(tc['arguments'])[:200]}")
    print(f"--- raw text (first 600 chars) ---")
    print(text[:600])


def main():
    engine = InstrumentedEngine(
        model=MODEL, dtype="bfloat16", max_model_len=4096,
        gpu_memory_utilization=0.6, tensor_parallel_size=1,
    )
    probe(engine, "data_analysis", DA["taxi_04"])
    probe(engine, "sql",           SQL_T[0])
    probe(engine, "rag",           RAG_T[0])


if __name__ == "__main__":
    main()
