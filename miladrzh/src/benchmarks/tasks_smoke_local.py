"""
Smoke test: load vLLM once via InstrumentedEngine and run one task from
each of data_analysis, sql, rag. max_turns=2 so it kills itself quickly.
Goal: confirm each agent produces sensible tool calls, not quality.
"""

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACES_DIR = os.path.join(ROOT, "traces")
sys.path.insert(0, ROOT)

# MIG workaround before vLLM loads
if "MIG-" in os.environ.get("CUDA_VISIBLE_DEVICES", ""):
    import vllm.platforms.cuda as _vcuda
    _vcuda.device_id_to_physical_device_id = lambda device_id=0: 0

from agent.vllm_engine import InstrumentedEngine
from agent.loop import run_task

MODEL = "Qwen/Qwen2.5-3B-Instruct"


def _hr(label):
    print(f"\n{'='*60}\n  {label}\n{'='*60}", flush=True)


def _save_trace(trace):
    os.makedirs(TRACES_DIR, exist_ok=True)
    path = os.path.join(TRACES_DIR, f"{trace['trace_id']}.json")
    with open(path, "w") as f:
        json.dump(trace, f, indent=2)
    print(f"  saved -> {path}")


def _summ_trace(trace):
    print(f"  outcome={trace['outcome']}  turns={len(trace['turns'])}")
    ans = trace.get("final_answer", "")
    if ans:
        print(f"  final_answer (first 400 chars):")
        for line in str(ans)[:400].splitlines():
            print(f"    | {line}")
    for i, turn in enumerate(trace["turns"]):
        tcs = turn.get("tool_calls", [])
        for tc in tcs:
            args = tc.get("args", {})
            name = tc.get("tool_name", "?")
            preview = str(args)[:120]
            print(f"    [turn {i}] {name}({preview})  {tc.get('duration_ms','?')}ms")
            result = tc.get("result", "")
            if result:
                for line in str(result).splitlines()[:4]:
                    print(f"         {line}")


def main():
    t0 = time.time()
    engine = InstrumentedEngine(
        model=MODEL,
        dtype="bfloat16",
        max_model_len=4096,
        gpu_memory_utilization=0.6,
        tensor_parallel_size=1,
    )
    print(f"[engine] loaded in {time.time()-t0:.1f}s", flush=True)

    # data_analysis — taxi_04 exists but parquet files may be heavy; try noaa_01
    from tasks.data_analysis import TASKS_BY_ID as DA
    da_id = "taxi_04"
    _hr(f"AGENT: data_analysis  ({da_id})")
    trace = run_task(DA[da_id], model=MODEL, engine=engine, max_turns=3)
    _summ_trace(trace)
    _save_trace(trace)

    # sql
    from tasks.sql import TASKS as SQL_T
    _hr(f"AGENT: sql  ({SQL_T[0]['id']})")
    trace = run_task(SQL_T[0], model=MODEL, engine=engine, max_turns=3)
    _summ_trace(trace)
    _save_trace(trace)

    # rag  — needs more turns because it must read search results + decide
    from tasks.rag import TASKS as RAG_T
    _hr(f"AGENT: rag  ({RAG_T[0]['id']})")
    trace = run_task(RAG_T[0], model=MODEL, engine=engine, max_turns=8)
    _summ_trace(trace)
    _save_trace(trace)

    print("\n[smoke] Done.")


if __name__ == "__main__":
    main()
