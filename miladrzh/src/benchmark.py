"""
Batch runner. Reads a curated tasks file (JSON list of task dicts) and runs
every task through the agent loop, saving one trace per task.

Companion to run.py:
  run.py        -> single task (or full TASKS list of one dataset module)
  benchmark.py  -> a set of tasks from a curated JSON file

Modes:
  --concurrency 1  (default) — sequential, sync engine. Same as run.py.
  --concurrency N  (N > 1)   — async engine + asyncio.gather. N agent
                                loops in flight at once, sharing one
                                vLLM engine. Saves a sibling
                                engine_trace.json with sampled engine
                                state across the whole batch.

Examples:
  # Sequential
  python benchmark.py --benchmark hotpotqa \
      --model Qwen/Qwen2.5-3B-Instruct --engine local \
      --max-model-len 16384 --gpu-memory-utilization 0.7 --max-turns 24

  # Concurrent (5 agents at once)
  python benchmark.py --benchmark hotpotqa --limit 80 \
      --model Qwen/Qwen2.5-3B-Instruct --engine local \
      --concurrency 5 --max-num-seqs 8 \
      --max-model-len 16384 --gpu-memory-utilization 0.7 --max-turns 24
"""

import argparse
import asyncio
import json
import os
import sys
import time

from config import PREFIX_CACHING

ROOT = os.path.dirname(os.path.abspath(__file__))
TRACES_DIR = os.path.join(ROOT, "traces")


def load_tasks_file(path):
    if not os.path.exists(path):
        print(f"Tasks file not found: {path}")
        sys.exit(1)
    with open(path, "r") as f:
        tasks = json.load(f)
    if not isinstance(tasks, list):
        print(f"Tasks file must contain a JSON list, got: {type(tasks).__name__}")
        sys.exit(1)
    for i, t in enumerate(tasks):
        for required in ("id", "agent_type", "prompt"):
            if required not in t:
                print(f"Task {i} missing required field '{required}': {t}")
                sys.exit(1)
    return tasks


def resolve_tasks_file(args):
    if args.tasks_file:
        return args.tasks_file
    if args.benchmark:
        return os.path.join(ROOT, "benchmarks", args.benchmark, "tasks.json")
    print("Provide --tasks-file or --benchmark.")
    sys.exit(1)


def build_sync_engine(args):
    if args.engine == "local":
        print(f"[engine] Loading vLLM locally (sync): {args.model}")
        from agent.vllm_engine import InstrumentedEngine
        engine = InstrumentedEngine(
            model=args.model,
            dtype="bfloat16",
            max_model_len=args.max_model_len,
            gpu_memory_utilization=args.gpu_memory_utilization,
            tensor_parallel_size=args.tensor_parallel_size,
            enable_prefix_caching=PREFIX_CACHING,
        )
        snap = engine.get_kv_snapshot()
        print(f"[engine] Ready. GPU blocks: {snap.get('total_gpu_blocks', '?')} total, "
              f"block_size={snap.get('block_size', '?')} tokens\n")
        return engine
    else:
        print(f"[engine] HTTP mode -> {args.vllm_url}\n")
        return args.vllm_url


def build_async_engine(args):
    # NOTE: tried scheduling_policy="priority" for hyperagent mode but vLLM
    # 0.7.3 hits an internal assertion (get_last_token_latency in prefill)
    # under any non-trivial concurrency. Disabled. Cooperative overlap at
    # tool-call boundaries produces the throughput win on its own.
    scheduling_policy = None
    print(f"[engine] Loading vLLM locally (async): {args.model}")
    print(f"         max_num_seqs={args.max_num_seqs}  "
          f"max_model_len={args.max_model_len}  "
          f"gpu_memory_utilization={args.gpu_memory_utilization}  "
          f"scheduling_policy={scheduling_policy}")
    from agent.vllm_engine import AsyncInstrumentedEngine
    engine = AsyncInstrumentedEngine(
        model=args.model,
        dtype="bfloat16",
        max_model_len=args.max_model_len,
        gpu_memory_utilization=args.gpu_memory_utilization,
        tensor_parallel_size=args.tensor_parallel_size,
        enable_prefix_caching=PREFIX_CACHING,
        max_num_seqs=args.max_num_seqs,
        scheduling_policy=scheduling_policy,
    )
    snap = engine.get_kv_snapshot()
    print(f"[engine] Ready. GPU blocks: {snap.get('total_gpu_blocks', '?')} total, "
          f"block_size={snap.get('block_size', '?')} tokens\n")
    return engine


def trace_summary_line(label, trace, elapsed):
    events = trace["events"]
    prefills   = [e for e in events if e["type"] == "prefill"]
    decodes    = [e for e in events if e["type"] == "decode"]
    tool_calls = [e for e in events if e["type"] == "tool_call"]
    durations  = [e["duration_ms"] for e in tool_calls]
    max_dur    = max(durations) / 1000 if durations else 0
    total_token = events[-1].get("total_token", 0) if events else 0
    return (f"{label}  outcome={trace['outcome']}  turns={len(prefills)}  "
            f"decodes={len(decodes)}  tool_calls={len(tool_calls)}  "
            f"max_tool={max_dur:.1f}s  total_token={total_token}  "
            f"wall={elapsed:.1f}s")


# ---------------------------------------------------------------------------
# Sync (concurrency=1) path
# ---------------------------------------------------------------------------

def run_sync(args, tasks):
    os.makedirs(args.output_dir, exist_ok=True)
    engine = build_sync_engine(args)
    print(f"Running {len(tasks)} task(s)  concurrency=1")
    print(f"  traces -> {args.output_dir}\n")
    from agent.loop import run_task

    n_ok = n_err = 0
    t_batch = time.time()
    for i, task in enumerate(tasks):
        print(f"[{i+1}/{len(tasks)}] {task['id']} ...", flush=True)
        t0 = time.time()
        try:
            trace = run_task(task, model=args.model, engine=engine,
                             max_turns=args.max_turns)
        except Exception as e:
            import traceback
            print(f"  ERROR: {e}")
            traceback.print_exc()
            n_err += 1
            continue
        elapsed = time.time() - t0
        print("  " + trace_summary_line("", trace, elapsed))
        out_path = os.path.join(args.output_dir, f"{trace['trace_id']}.json")
        with open(out_path, "w") as f:
            json.dump(trace, f, indent=2)
        print(f"  saved -> {out_path}")
        n_ok += 1
    dt = time.time() - t_batch
    print(f"\nDone. ok={n_ok}  errors={n_err}  total_wall={dt:.1f}s")


# ---------------------------------------------------------------------------
# Async (concurrency>1) path
# ---------------------------------------------------------------------------

async def _engine_sampler(engine, samples, t_start, stop_event, period_s=0.1):
    """Background coroutine: sample engine state every period_s seconds."""
    while not stop_event.is_set():
        ts = time.time()
        snap = engine.get_kv_snapshot()
        sched = engine._get_scheduler()
        try:
            running = len(getattr(sched, "running", []))
            waiting = len(getattr(sched, "waiting", []))
            swapped = len(getattr(sched, "swapped", []))
        except Exception:
            running = waiting = swapped = -1
        samples.append({
            "t_rel_ms":        round((ts - t_start) * 1000, 1),
            "kv_tokens_used":  snap.get("kv_tokens_used", 0),
            "used_gpu_blocks": snap.get("used_gpu_blocks", 0),
            "free_gpu_blocks": snap.get("free_gpu_blocks", 0),
            "total_gpu_blocks": snap.get("total_gpu_blocks", 0),
            "running":         running,
            "waiting":         waiting,
            "swapped":         swapped,
        })
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=period_s)
        except asyncio.TimeoutError:
            pass


async def _run_one(task, args, engine, semaphore, summaries, t_batch_start):
    """Single agent coroutine. Records three timestamps:
       submit  = coroutine entered (before semaphore acquire)
       acquire = semaphore acquired (worker slot active)
       end     = task done
    so we can compute service latency (end - acquire) and total latency
    (end - submit). queue latency = acquire - submit.
    """
    from agent.loop import run_task_async
    t_submit = time.time()
    submit_rel = round((t_submit - t_batch_start) * 1000, 1)
    async with semaphore:
        t_acquire = time.time()
        acquire_rel = round((t_acquire - t_batch_start) * 1000, 1)
        try:
            trace = await run_task_async(task, model=args.model, engine=engine,
                                         max_turns=args.max_turns)
        except Exception as e:
            import traceback
            traceback.print_exc()
            t_end = time.time()
            end_rel = round((t_end - t_batch_start) * 1000, 1)
            summaries.append({
                "task_id":   task["id"],
                "outcome":   f"ERROR: {e}",
                "submit_rel_ms":  submit_rel,
                "acquire_rel_ms": acquire_rel,
                "end_rel_ms":     end_rel,
                "queue_ms":   round((t_acquire - t_submit) * 1000, 1),
                "service_ms": round((t_end - t_acquire) * 1000, 1),
                "total_ms":   round((t_end - t_submit) * 1000, 1),
            })
            print(f"  [{task['id']}] ERROR: {e}", flush=True)
            return

        t_end = time.time()
        elapsed = t_end - t_acquire
        end_rel = round((t_end - t_batch_start) * 1000, 1)
        out_path = os.path.join(args.output_dir, f"{trace['trace_id']}.json")
        with open(out_path, "w") as f:
            json.dump(trace, f, indent=2)
        summaries.append({
            "task_id":   task["id"],
            "outcome":   trace["outcome"],
            "trace_path": os.path.relpath(out_path, args.output_dir),
            "submit_rel_ms":  submit_rel,
            "acquire_rel_ms": acquire_rel,
            "end_rel_ms":     end_rel,
            "queue_ms":   round((t_acquire - t_submit) * 1000, 1),
            "service_ms": round((t_end - t_acquire) * 1000, 1),
            "total_ms":   round((t_end - t_submit) * 1000, 1),
        })
        print("  " + trace_summary_line(f"[{task['id']}]", trace, elapsed), flush=True)


async def _warmup(engine, n_iters=2):
    """Run a few discardable generations to warm up cudagraphs, attention
    kernels, and any lazy CUDA allocations before timed measurement starts.
    Three prompt sizes per iter (short / medium / long-ish) so multiple
    cudagraph buckets are touched. Output is discarded."""
    print("[warmup] running prefill+generate warmup ...", flush=True)
    t0 = time.time()
    sizes = [
        "Hello.",
        "Summarize this paragraph: " + ("the quick brown fox jumps over the lazy dog. " * 40),
        "Explain in detail: " + ("modern transformer architectures use multi-head attention. " * 200),
    ]
    for it in range(n_iters):
        for j, p in enumerate(sizes):
            msgs = [{"role": "user", "content": p}]
            try:
                await engine.generate_turn(messages=msgs, tools=[],
                                           max_tokens=64,
                                           request_id=f"warmup_{it}_{j}")
            except Exception as e:
                print(f"[warmup] iter {it} prompt {j} failed: {e}", flush=True)
    print(f"[warmup] done in {time.time() - t0:.1f}s", flush=True)


async def run_async(args, tasks):
    os.makedirs(args.output_dir, exist_ok=True)
    engine = build_async_engine(args)
    if not args.no_warmup:
        await _warmup(engine, n_iters=args.warmup_iters)
    print(f"Running {len(tasks)} task(s)  concurrency={args.concurrency}  max_num_seqs={args.max_num_seqs}")
    print(f"  traces -> {args.output_dir}\n")

    semaphore = asyncio.Semaphore(args.concurrency)
    summaries = []
    samples = []
    stop_event = asyncio.Event()
    t_batch_start = time.time()

    sampler = asyncio.create_task(
        _engine_sampler(engine, samples, t_batch_start, stop_event,
                        period_s=args.sample_period_s)
    )

    workers = [_run_one(t, args, engine, semaphore, summaries, t_batch_start)
               for t in tasks]
    await asyncio.gather(*workers, return_exceptions=False)

    stop_event.set()
    await sampler

    t_batch_end = time.time()
    total_wall_s = round(t_batch_end - t_batch_start, 2)

    def _stats(values):
        if not values:
            return {"n": 0}
        s = sorted(values)
        n = len(s)
        def _pct(p):
            if n == 1:
                return s[0]
            k = (n - 1) * p
            f = int(k)
            c = min(f + 1, n - 1)
            return s[f] + (s[c] - s[f]) * (k - f)
        return {
            "n":      n,
            "mean":   round(sum(s) / n, 1),
            "p50":    round(_pct(0.50), 1),
            "p90":    round(_pct(0.90), 1),
            "p99":    round(_pct(0.99), 1),
            "min":    round(s[0], 1),
            "max":    round(s[-1], 1),
        }

    ok = [s for s in summaries if not str(s["outcome"]).startswith("ERROR")]
    n_ok  = len(ok)
    n_err = len(summaries) - n_ok

    batch_meta = {
        "model":        args.model,
        "concurrency":  args.concurrency,
        "max_num_seqs": args.max_num_seqs,
        "max_model_len": args.max_model_len,
        "gpu_memory_utilization": args.gpu_memory_utilization,
        "prefix_caching": PREFIX_CACHING,
        "max_turns":    args.max_turns,
        "n_tasks":      len(tasks),
        "n_ok":         n_ok,
        "n_err":        n_err,
        "tasks_file":   resolve_tasks_file(args),
        "start_ts":     t_batch_start,
        "end_ts":       t_batch_end,
        "total_wall_s": total_wall_s,
        "sample_period_s": args.sample_period_s,
        "n_samples":    len(samples),
    }

    summary = {
        "batch_meta": batch_meta,
        "latency_ms_total":   _stats([s["total_ms"]   for s in ok]),
        "latency_ms_service": _stats([s["service_ms"] for s in ok]),
        "latency_ms_queue":   _stats([s["queue_ms"]   for s in ok]),
    }
    sum_path = os.path.join(args.output_dir, "batch_summary.json")
    with open(sum_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nbatch summary -> {sum_path}")
    lt = summary["latency_ms_total"]; ls = summary["latency_ms_service"]
    print(f"  total   latency: mean={lt.get('mean','-')} ms  p90={lt.get('p90','-')} ms")
    print(f"  service latency: mean={ls.get('mean','-')} ms  p90={ls.get('p90','-')} ms")

    engine_trace = {
        "batch_meta": batch_meta,
        "task_summaries": sorted(summaries, key=lambda s: s["submit_rel_ms"]),
        "samples": samples,
    }
    eng_path = os.path.join(args.output_dir, "engine_trace.json")
    with open(eng_path, "w") as f:
        json.dump(engine_trace, f, indent=2)
    print(f"engine trace  -> {eng_path}")

    print(f"Done. ok={n_ok}  errors={n_err}  total_wall={total_wall_s}s")


# ---------------------------------------------------------------------------
# Hyperagent path
# ---------------------------------------------------------------------------

async def run_hyperagent_mode(args, tasks):
    os.makedirs(args.output_dir, exist_ok=True)
    engine = build_async_engine(args)
    if not args.no_warmup:
        await _warmup(engine, n_iters=args.warmup_iters)

    print(f"Running {len(tasks)} task(s)  mode=hyperagent  workers={args.workers}  "
          f"max_num_seqs={args.max_num_seqs}")
    print(f"  traces -> {args.output_dir}\n")

    samples    = []
    stop_event = asyncio.Event()

    from agent.hyperagent import run_hyperagent
    sampler_started_at = time.time()
    sampler = asyncio.create_task(
        _engine_sampler(engine, samples, sampler_started_at, stop_event,
                        period_s=args.sample_period_s)
    )

    try:
        result = await run_hyperagent(args, tasks, engine)
    finally:
        stop_event.set()
        await sampler

    summaries     = result["summaries"]
    switch_log    = result["switch_log"]
    t_batch_start = result["t_batch_start"]
    t_batch_end   = result["t_batch_end"]
    total_wall_s  = round(t_batch_end - t_batch_start, 2)

    def _stats(values):
        if not values:
            return {"n": 0}
        s = sorted(values); n = len(s)
        def _pct(p):
            if n == 1: return s[0]
            k = (n - 1) * p; f = int(k); c = min(f + 1, n - 1)
            return s[f] + (s[c] - s[f]) * (k - f)
        return {
            "n":    n,
            "mean": round(sum(s) / n, 1),
            "p50":  round(_pct(0.50), 1),
            "p90":  round(_pct(0.90), 1),
            "p99":  round(_pct(0.99), 1),
            "min":  round(s[0], 1),
            "max":  round(s[-1], 1),
        }

    ok    = [s for s in summaries if not str(s["outcome"]).startswith("ERROR")]
    n_ok  = len(ok)
    n_err = len(summaries) - n_ok

    batch_meta = {
        "model":         args.model,
        "mode":          "hyperagent",
        "workers":       args.workers,
        "max_num_seqs":  args.max_num_seqs,
        "max_model_len": args.max_model_len,
        "gpu_memory_utilization": args.gpu_memory_utilization,
        "prefix_caching": PREFIX_CACHING,
        "max_turns":     args.max_turns,
        "n_tasks":       len(tasks),
        "n_ok":          n_ok,
        "n_err":         n_err,
        "tasks_file":    resolve_tasks_file(args),
        "start_ts":      t_batch_start,
        "end_ts":        t_batch_end,
        "total_wall_s":  total_wall_s,
        "n_samples":     len(samples),
    }
    summary = {
        "batch_meta":         batch_meta,
        "latency_ms_total":   _stats([s["total_ms"]   for s in ok]),
        "latency_ms_service": _stats([s["service_ms"] for s in ok if s["service_ms"] is not None]),
        "throughput_tasks_per_s": round(n_ok / total_wall_s, 3) if total_wall_s > 0 else None,
    }
    sum_path = os.path.join(args.output_dir, "batch_summary.json")
    with open(sum_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nbatch summary -> {sum_path}")
    lt = summary["latency_ms_total"]; ls = summary["latency_ms_service"]
    print(f"  total   latency: mean={lt.get('mean','-')} ms  p90={lt.get('p90','-')} ms  p99={lt.get('p99','-')} ms")
    print(f"  service latency: mean={ls.get('mean','-')} ms  p90={ls.get('p90','-')} ms  p99={ls.get('p99','-')} ms")
    print(f"  throughput     : {summary['throughput_tasks_per_s']} tasks/s")

    sw_path = os.path.join(args.output_dir, "switch_log.json")
    with open(sw_path, "w") as f:
        json.dump({"batch_meta": batch_meta, "events": switch_log}, f, indent=2)
    print(f"switch log     -> {sw_path}")

    eng_path = os.path.join(args.output_dir, "engine_trace.json")
    with open(eng_path, "w") as f:
        json.dump({"batch_meta": batch_meta,
                   "task_summaries": sorted(summaries, key=lambda s: s["submit_rel_ms"]),
                   "samples": samples}, f, indent=2)
    print(f"engine trace   -> {eng_path}")
    print(f"Done. ok={n_ok}  errors={n_err}  total_wall={total_wall_s}s")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks-file", default=None)
    parser.add_argument("--benchmark",  default=None)
    parser.add_argument("--model",      required=True)
    parser.add_argument("--engine",     default="local", choices=["http", "local"])
    parser.add_argument("--vllm-url",   default="http://localhost:8000/v1")
    parser.add_argument("--max-turns",  type=int, default=None)
    parser.add_argument("--output-dir", default=TRACES_DIR)
    parser.add_argument("--limit",      type=int, default=None)
    parser.add_argument("--start",      type=int, default=0)
    parser.add_argument("--mode",        default="plain", choices=["plain", "hyperagent"],
                        help="plain = current behavior (one task per worker). "
                             "hyperagent = each worker owns 2 partner tasks; "
                             "switches on tool-call boundaries; relies on "
                             "vLLM CPU-swap preemption (max_num_seqs == workers).")
    parser.add_argument("--workers",     type=int, default=1,
                        help="Number of hyperagent workers (mode=hyperagent only).")
    parser.add_argument("--concurrency", type=int, default=1,
                        help="Number of agent loops in flight at once. >1 uses async engine.")
    parser.add_argument("--max-num-seqs", type=int, default=None,
                        help="vLLM max_num_seqs. If unset, defaults to max(concurrency*2, 8) in async mode.")
    parser.add_argument("--sample-period-s", type=float, default=0.1,
                        help="Engine-trace sampling period in seconds (async mode).")
    parser.add_argument("--tensor-parallel-size",    type=int,   default=1)
    parser.add_argument("--max-model-len",           type=int,   default=32768)
    parser.add_argument("--gpu-memory-utilization",  type=float, default=0.9)
    parser.add_argument("--no-warmup", action="store_true",
                        help="Skip the warmup pass before timed measurement.")
    parser.add_argument("--warmup-iters", type=int, default=2,
                        help="Number of warmup iterations (each runs 3 prompts of varying size).")
    args = parser.parse_args()

    tasks_file = resolve_tasks_file(args)
    tasks = load_tasks_file(tasks_file)
    if args.start:
        tasks = tasks[args.start:]
    if args.limit is not None:
        tasks = tasks[:args.limit]
    if not tasks:
        print("No tasks to run after applying --start/--limit.")
        sys.exit(0)

    if args.mode == "hyperagent":
        if args.engine != "local":
            print("--mode hyperagent requires --engine local.")
            sys.exit(1)
        # Force max_num_seqs == workers so a partner returning from tool
        # actually preempts the other partner via vLLM swap_space.
        args.max_num_seqs = args.workers
        asyncio.run(run_hyperagent_mode(args, tasks))
    elif args.engine == "local":
        # Always use async path on local engine so engine_trace.json is
        # written even at concurrency=1. Semaphore=1 is just sequential.
        if args.max_num_seqs is None:
            args.max_num_seqs = max(args.concurrency * 2, 8)
        asyncio.run(run_async(args, tasks))
    elif args.concurrency <= 1:
        run_sync(args, tasks)
    else:
        print("--concurrency > 1 requires --engine local (async vLLM).")
        sys.exit(1)


if __name__ == "__main__":
    main()
