"""
Entry point. Runs agent tasks and saves traces to traces/.

Two engine modes:

  --engine http  (default)
      Calls a running vLLM OpenAI-compatible server.
      Also pass --vllm-url if the server is not at localhost:8000.
      KV counts are estimated from prompt_tokens (proxy).

  --engine local
      Imports vLLM as a Python library. The model runs in the same process.
      KV counts come from the real BlockSpaceManager. Starts slower (model
      load) but gives publishable instrumentation data.
      Pass --tensor-parallel-size N to use N GPUs.

Examples:
    # HTTP mode (vLLM server already running)
    python run.py --dataset data_analysis --model meta-llama/Llama-3.1-8B-Instruct

    # Local mode (vLLM as library, real KV counts)
    python run.py --dataset data_analysis --model meta-llama/Llama-3.1-8B-Instruct \
        --engine local

    # Single task
    python run.py --dataset swe_bench --task sympy__sympy-12419 \
        --model meta-llama/Llama-3.1-8B-Instruct --engine local
"""

import argparse
import json
import os
import sys
import time

from config import PREFIX_CACHING

ROOT = os.path.dirname(os.path.abspath(__file__))
TRACES_DIR = os.path.join(ROOT, "traces")


def load_tasks(dataset, task_id):
    if dataset == "data_analysis":
        from tasks.data_analysis import TASKS, TASKS_BY_ID
    elif dataset == "sql":
        from tasks.sql import TASKS, TASKS_BY_ID
    elif dataset == "rag":
        from tasks.rag import TASKS, TASKS_BY_ID
    elif dataset == "hotpotqa":
        from tasks.hotpotqa import TASKS, TASKS_BY_ID
    elif dataset == "browsecomp":
        from tasks.browsecomp import TASKS, TASKS_BY_ID
    elif dataset == "swe_bench":
        from tasks.swe_bench import TASKS, TASKS_BY_ID
    else:
        print(f"Unknown dataset: {dataset}. Available: data_analysis, sql, rag, hotpotqa, browsecomp, swe_bench")
        sys.exit(1)

    if task_id:
        if task_id not in TASKS_BY_ID:
            print(f"Unknown task: {task_id}. Available: {list(TASKS_BY_ID)}")
            sys.exit(1)
        return [TASKS_BY_ID[task_id]]
    return TASKS


def build_engine(args):
    """Return an InstrumentedEngine (local) or a URL string (http)."""
    if args.engine == "local":
        print(f"[engine] Loading vLLM locally: {args.model}")
        print(f"         tensor_parallel_size={args.tensor_parallel_size}  "
              f"max_model_len={args.max_model_len}  "
              f"gpu_memory_utilization={args.gpu_memory_utilization}")
        from agent.vllm_engine import InstrumentedEngine
        engine = InstrumentedEngine(
            model=args.model,
            dtype="bfloat16",
            max_model_len=args.max_model_len,
            gpu_memory_utilization=args.gpu_memory_utilization,
            tensor_parallel_size=args.tensor_parallel_size,
            enable_prefix_caching=PREFIX_CACHING,
        )
        print(f"         enable_prefix_caching={engine.prefix_caching_enabled}")
        snap = engine.get_kv_snapshot()
        print(f"[engine] Ready. GPU blocks: {snap.get('total_gpu_blocks', '?')} total, "
              f"block_size={snap.get('block_size', '?')} tokens\n")
        return engine
    else:
        print(f"[engine] HTTP mode -> {args.vllm_url}\n")
        return args.vllm_url


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset",   default="data_analysis",
                        choices=["data_analysis", "sql", "rag", "hotpotqa", "browsecomp", "swe_bench"])
    parser.add_argument("--task",      default=None,  help="Run a single task by ID")
    parser.add_argument("--model",     required=True, help="HuggingFace model ID")
    parser.add_argument("--engine",    default="http", choices=["http", "local"],
                        help="'http' = call a vLLM server; 'local' = embed vLLM in-process")
    parser.add_argument("--vllm-url",  default="http://localhost:8000/v1",
                        help="vLLM server URL (http mode only)")
    parser.add_argument("--max-turns", type=int, default=None,
                        help="Override turn budget. If omitted, use per-agent "
                             "default (or task['max_turns'] if set).")
    parser.add_argument("--output-dir", default=TRACES_DIR)
    parser.add_argument("--limit",      type=int, default=None,
                        help="Run only the first N tasks from the loaded dataset.")
    # Local engine options
    parser.add_argument("--tensor-parallel-size",    type=int,   default=1)
    parser.add_argument("--max-model-len",           type=int,   default=32768)
    parser.add_argument("--gpu-memory-utilization",  type=float, default=0.9)
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    tasks  = load_tasks(args.dataset, args.task)
    if args.limit is not None:
        tasks = tasks[:args.limit]
    engine = build_engine(args)

    print(f"Running {len(tasks)} task(s) | model={args.model} | engine={args.engine}")
    print(f"Traces -> {args.output_dir}\n")

    from agent.loop import run_task

    swe_bench_mode = args.dataset == "swe_bench"
    if swe_bench_mode:
        from tasks.swe_bench import setup_workspace, cleanup_workspace
        from benchmarks.swe_bench.grade import grade as swe_grade

    for i, task in enumerate(tasks):
        print(f"[{i+1}/{len(tasks)}] {task['id']} ...", flush=True)
        t0 = time.time()

        if swe_bench_mode:
            try:
                workspace = setup_workspace(task)
                print(f"  workspace -> {workspace}")
            except Exception as e:
                print(f"  SETUP ERROR: {e}")
                continue

        try:
            trace = run_task(
                task,
                model=args.model,
                engine=engine,
                max_turns=args.max_turns,
            )
        except Exception as e:
            import traceback
            print(f"  ERROR: {e}")
            traceback.print_exc()
            if swe_bench_mode:
                cleanup_workspace(task)
            continue

        elapsed    = time.time() - t0
        events     = trace["events"]
        prefills   = [e for e in events if e["type"] == "prefill"]
        decodes    = [e for e in events if e["type"] == "decode"]
        tool_calls = [e for e in events if e["type"] == "tool_call"]
        durations  = [e["duration_ms"] for e in tool_calls]
        max_dur    = max(durations) / 1000 if durations else 0
        total_token = events[-1].get("total_token", 0) if events else 0

        print(f"  outcome={trace['outcome']}  turns={len(prefills)}  "
              f"decodes={len(decodes)}  tool_calls={len(tool_calls)}  "
              f"max_tool={max_dur:.1f}s  total_token={total_token}  "
              f"wall={elapsed:.1f}s")

        if swe_bench_mode:
            try:
                gr = swe_grade(task)
                trace["correctness_grader"] = gr
                if "error" in gr:
                    print(f"  grader: {gr['error']}")
                else:
                    print(f"  grader: resolved={gr['resolved']}  "
                          f"f2p={gr['fail_to_pass_passed']}/{gr['fail_to_pass_total']}  "
                          f"p2p={gr['pass_to_pass_passed']}/{gr['pass_to_pass_total']}  "
                          f"regressions={len(gr['pass_to_pass_regressions'])}")
            except Exception as e:
                trace["correctness_grader"] = {"error": str(e)}
                print(f"  grader EXCEPTION: {e}")

        out_path = os.path.join(args.output_dir, f"{trace['trace_id']}.json")
        with open(out_path, "w") as f:
            json.dump(trace, f, indent=2)
        print(f"  saved -> {out_path}")

        if swe_bench_mode:
            cleanup_workspace(task)

    print("\nDone.")


if __name__ == "__main__":
    main()
