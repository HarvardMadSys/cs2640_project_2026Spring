import argparse
import json
import time
import logging
import asyncio
from typing import List, Tuple, Dict, Any
from tqdm import tqdm
import os
import multiprocessing

# Set environment variable for vLLM to use 'spawn' method
os.environ['VLLM_WORKER_MULTIPROC_METHOD'] = 'spawn'

# Set the global multiprocessing start method
try:
    multiprocessing.set_start_method('spawn', force=True)
except RuntimeError:
    pass

logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)


def parse_cache_size(size_str: str) -> int:
    """Parse cache size from string format (e.g., '1G', '500M', '1024')."""
    size_str = size_str.lower()
    if size_str.endswith('g'):
        return int(float(size_str[:-1]) * 1024 * 1024 * 1024)
    if size_str.endswith('m'):
        return int(float(size_str[:-1]) * 1024 * 1024)
    if size_str.endswith('k'):
        return int(float(size_str[:-1]) * 1024)
    return int(size_str)


def synthesize_token_ids(hash_ids, block_size, vocab_size):
    """
    Synthesize token IDs from hash IDs.

    For each hash ID, we generate block_size tokens by treating the hash as a seed.
    This ensures deterministic token generation from the same hash ID.
    """
    synthesized_token_ids = []
    for h in hash_ids:
        current_seed = h
        for _ in range(block_size):
            token_id = (current_seed + 1) % vocab_size
            synthesized_token_ids.append(token_id)
            current_seed = current_seed // vocab_size + 1

    return synthesized_token_ids


def load_trace(trace_file, cache_size, block_size, vocab_size, request_limit=None):
    """
    Load trace file and generate prompts with synthesized token IDs.

    Returns:
        List of tuples: [(prompt_token_ids, output_length, metadata), ...]
    """
    prompts = []

    with open(trace_file, 'r') as f:
        for line_num, line in enumerate(f):
            if request_limit is not None and len(prompts) >= request_limit:
                break

            line = line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError as e:
                logger.warning(f"Skipping line {line_num}: invalid JSON - {e}")
                continue

            hash_ids = data.get("hash_ids", [])
            input_length = data.get("input_length", 0)
            output_length = data.get("output_length", 0)

            if not hash_ids:
                logger.debug(f"Skipping line {line_num}: no hash_ids")
                continue

            # Skip requests that need more blocks than cache size (minus 1 for the null block)
            if len(hash_ids) > cache_size - 1:
                logger.debug(f"Skipping line {line_num}: requires {len(hash_ids)} blocks, cache only has {cache_size - 1} usable blocks")
                continue

            # Synthesize token IDs from hash IDs
            prompt_token_ids = synthesize_token_ids(hash_ids, block_size, vocab_size)

            # Truncate to exact input_length (last block may not be full)
            if len(prompt_token_ids) > input_length:
                prompt_token_ids = prompt_token_ids[:input_length]
            elif len(prompt_token_ids) < input_length:
                logger.warning(
                    f"Line {line_num}: synthesized {len(prompt_token_ids)} tokens "
                    f"but input_length is {input_length}. Using synthesized length."
                )

            metadata = {
                "chat_id": data.get("chat_id", line_num),
                "input_length": len(prompt_token_ids),
                "expected_output_length": output_length,
                "num_blocks": len(hash_ids),
            }

            prompts.append((prompt_token_ids, output_length, metadata))

    return prompts


async def process_requests_async(
    llm,
    prompts: List[Tuple[List[int], int, Dict[str, Any]]],
    sampling_params,
    args,
    max_concurrent: int = 100
):
    """
    Process requests asynchronously with a concurrency limit to avoid overloading vLLM.
    When max_concurrent=1, requests are processed in strict FIFO order.

    Args:
        llm: AsyncLLMEngine instance
        prompts: List of (prompt_token_ids, output_length, metadata) tuples
        sampling_params: SamplingParams for generation
        args: Command line arguments
        max_concurrent: Maximum number of concurrent requests
    """
    import torch

    # Track statistics
    stats = {
        "completed": 0,
        "total_prompt_tokens": 0,
        "total_output_tokens": 0,
        "latencies": [],
        "ttfts": [],
        "tpots": [],
        "start_time": None,
        "end_time": None,
    }

    async def process_single_request(i, prompt_token_ids, output_length, metadata):
        """Process a single request."""
        # Prepare prompt
        prompt_input = {"prompt_token_ids": prompt_token_ids}

        # Time the generation
        start = time.perf_counter()

        try:
            # Submit async request and consume the stream
            final_output = None
            request_id = f"request-{i}"
            async for output in llm.generate(
                prompt=prompt_input,
                sampling_params=sampling_params,
                request_id=request_id,
            ):
                final_output = output

            end = time.perf_counter()

            latency = end - start

            # Update statistics
            stats["completed"] += 1
            stats["total_prompt_tokens"] += len(prompt_token_ids)
            if final_output and hasattr(final_output, 'outputs'):
                # Count actual output tokens
                output_tokens = sum(len(o.token_ids) for o in final_output.outputs)
                stats["total_output_tokens"] += output_tokens
            else:
                stats["total_output_tokens"] += output_length
            stats["latencies"].append(latency)

            return {
                "index": i,
                "output": final_output,
                "latency": latency,
                "metadata": metadata,
                "prompt_len": len(prompt_token_ids),
            }
        except Exception as e:
            logger.error(f"Request {i} failed: {e}")
            raise

    # Record overall start time
    stats["start_time"] = time.perf_counter()

    results = []

    # Process with controlled concurrency in FIFO order
    # No semaphore needed - we control concurrency by managing task submission
    stats_lock = asyncio.Lock()

    async def process_with_stats(i, prompt_token_ids, output_length, metadata):
        """Process a single request and update statistics."""
        # Prepare prompt
        prompt_input = {"prompt_token_ids": prompt_token_ids}

        # Build per-request sampling params so we can honor the trace's
        # output_length when --output-length is not explicitly set.
        if args.prefill_only:
            request_max_tokens = 1
        elif args.output_length is not None:
            request_max_tokens = args.output_length
        else:
            request_max_tokens = max(int(output_length), 1)
        request_sampling_params = sampling_params.clone()
        request_sampling_params.max_tokens = request_max_tokens

        # Time the generation
        start = time.perf_counter()

        try:
            # Submit async request and consume the stream
            final_output = None
            request_id = f"request-{i}"
            first_token_time = None
            async for output in llm.generate(
                prompt=prompt_input,
                sampling_params=request_sampling_params,
                request_id=request_id,
            ):
                if first_token_time is None and getattr(output, "outputs", None):
                    if any(len(o.token_ids) > 0 for o in output.outputs):
                        first_token_time = time.perf_counter()
                final_output = output

            end = time.perf_counter()

            latency = end - start

            # Compute TTFT and TPOT
            output_tokens = 0
            if final_output and hasattr(final_output, 'outputs'):
                output_tokens = sum(len(o.token_ids) for o in final_output.outputs)
            ttft = (first_token_time - start) if first_token_time is not None else None
            if output_tokens > 1 and first_token_time is not None:
                tpot = (end - first_token_time) / (output_tokens - 1)
            else:
                tpot = None

            # Update statistics with lock
            async with stats_lock:
                stats["completed"] += 1
                stats["total_prompt_tokens"] += len(prompt_token_ids)
                if output_tokens > 0:
                    stats["total_output_tokens"] += output_tokens
                else:
                    stats["total_output_tokens"] += output_length
                stats["latencies"].append(latency)
                if ttft is not None:
                    stats["ttfts"].append(ttft)
                if tpot is not None:
                    stats["tpots"].append(tpot)

            return {
                "index": i,
                "output": final_output,
                "latency": latency,
                "ttft": ttft,
                "tpot": tpot,
                "output_tokens": output_tokens,
                "metadata": metadata,
                "prompt_len": len(prompt_token_ids),
            }
        except Exception as e:
            logger.error(f"Request {i} failed: {e}")
            raise

    # Submit tasks in FIFO order while respecting concurrency limit
    pending_tasks = set()
    prompt_iter = enumerate(prompts)

    with tqdm(total=len(prompts), desc=f"Throughput test ({args.algo})") as pbar:
        # Start initial batch up to max_concurrent
        for _ in range(min(max_concurrent, len(prompts))):
            try:
                i, (prompt_token_ids, output_length, metadata) = next(prompt_iter)
                task = asyncio.create_task(process_with_stats(i, prompt_token_ids, output_length, metadata))
                pending_tasks.add(task)
            except StopIteration:
                break

        # Process tasks as they complete and submit new ones in order
        while pending_tasks:
            # Wait for at least one task to complete
            done, pending_tasks = await asyncio.wait(pending_tasks, return_when=asyncio.FIRST_COMPLETED)

            # Collect results from completed tasks
            for task in done:
                result = await task
                results.append(result)
                pbar.update(1)

            # Submit new tasks to maintain max_concurrent
            for _ in range(len(done)):
                try:
                    i, (prompt_token_ids, output_length, metadata) = next(prompt_iter)
                    new_task = asyncio.create_task(process_with_stats(i, prompt_token_ids, output_length, metadata))
                    pending_tasks.add(new_task)
                except StopIteration:
                    break

    # Sort results by index to maintain order in output
    results.sort(key=lambda x: x["index"])

    # Record overall end time
    stats["end_time"] = time.perf_counter()

    return results, stats


def main():
    import random
    import numpy as np
    import torch

    parser = argparse.ArgumentParser(description="Throughput benchmark for vLLM with trace file")
    parser.add_argument("--trace-file", required=True, help="Path to trace file (JSONL format)")
    parser.add_argument("--cache-size", required=True, help="Cache size (e.g., '1000' blocks)")
    parser.add_argument("--block-size", type=int, default=16, help="Block size in tokens (default: 16)")
    parser.add_argument("--request-limit", type=int, default=None, help="Limit number of requests to process")
    parser.add_argument("--algo", type=str, default="LRU", help="Eviction algorithm (LRU, LCD, GDCF, etc.)")
    parser.add_argument("--model", type=str, required=True, help="Model name or path")
    parser.add_argument("--max-model-len", type=int, default=220000, help="Maximum model length")
    parser.add_argument("--attention-backend", type=str, default="FLASHINFER", help="Attention backend (default: FLASHINFER)")
    parser.add_argument("--prefill-only", action="store_true", help="Only run prefill, set max_tokens=1")
    parser.add_argument("--latency-file", type=str, default=None, help="JSON file with latency curve for compute intensity")
    parser.add_argument("--output-file", type=str, default=None, help="Output file for throughput results (JSON)")
    parser.add_argument("--seed", type=int, default=None, help="Random seed (if not set, uses current time)")
    parser.add_argument("--max-concurrent", type=int, default=100, help="Maximum number of concurrent requests (default: 100)")
    parser.add_argument("--output-length", type=int, default=None,
                        help="Fixed number of decode tokens per request. If unset, uses each request's output_length from the trace.")

    args = parser.parse_args()

    # Set random seed
    if args.seed is None:
        seed = int(time.time() * 1000) % (2**32)
    else:
        seed = args.seed

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    print(f"Random seed set to: {seed}")

    # Parse cache size
    cache_size = parse_cache_size(args.cache_size)

    # Monkey-patch get_compute_intensity if latency file provided
    if args.latency_file:
        import vllm.v1.core.free_block_manager as free_block_manager

        with open(args.latency_file, 'r') as f:
            latency_data = json.load(f)
            a = latency_data["fitted_curve"]["a"]
            b = latency_data["fitted_curve"]["b"]

            def patched_get_compute_intensity(index: int) -> float:
                # Use middle of block for average latency
                seq_len = (index + 0.5) * args.block_size
                derivative = 2 * a * seq_len + b
                return float(1000.0 * derivative * args.block_size)

            free_block_manager.get_compute_intensity = patched_get_compute_intensity
            print(f"Patched get_compute_intensity (a={a:.3e}, b={b:.3e})")
            print(f"  idx=0 -> {patched_get_compute_intensity(0):.4f}ms")
            print(f"  idx=100 -> {patched_get_compute_intensity(100):.4f}ms")

    try:
        # Import vLLM after patching
        from vllm import AsyncLLMEngine, SamplingParams, AsyncEngineArgs

        # Support local file paths
        expanded_path = os.path.expanduser(args.model)
        model_id_or_path = os.path.abspath(expanded_path) if os.path.exists(expanded_path) else args.model

        print(f"Initializing AsyncLLMEngine with model={model_id_or_path}")
        print(f"  enable_prefix_caching=True")
        print(f"  num_gpu_blocks_override={cache_size}")
        print(f"  max_model_len={args.max_model_len}")
        print(f"  attention_backend={args.attention_backend}")
        print(f"  max_concurrent_requests={args.max_concurrent}")

        # Create engine args
        engine_args = AsyncEngineArgs(
            model=model_id_or_path,
            enable_prefix_caching=True,
            gpu_memory_utilization=0.9,
            tensor_parallel_size=1,
            max_model_len=args.max_model_len,
            trust_remote_code=True,
            attention_backend=args.attention_backend,
            num_gpu_blocks_override=cache_size,
            disable_log_stats=False,
            block_manager_algo=args.algo,
        )

        # Create async engine
        llm = AsyncLLMEngine.from_engine_args(engine_args)

        # Get vocab size from model config
        vocab_size = getattr(
            getattr(llm.model_config, "hf_config", None),
            "vocab_size",
            32000
        )
        print(f"Model vocab size: {vocab_size}")

        # Load trace and generate prompts
        print(f"Loading trace from {args.trace_file}")
        prompts = load_trace(
            args.trace_file,
            cache_size,
            args.block_size,
            vocab_size,
            args.request_limit
        )
        print(f"Loaded {len(prompts)} requests from trace")

        # Base sampling params; per-request max_tokens is set inside
        # process_with_stats so each request can honor its trace output_length.
        sampling_params = SamplingParams(
            max_tokens=1,
            temperature=0,
            ignore_eos=True,
        )
        if args.prefill_only:
            print("Mode: Prefill only (max_tokens=1)")
        elif args.output_length is not None:
            print(f"Mode: Prefill + {args.output_length} decode tokens (fixed)")
        else:
            print("Mode: Prefill + decode (max_tokens from trace output_length)")

        print(f"Processing {len(prompts)} requests with max {args.max_concurrent} concurrent...")

        # Run async processing
        results, stats = asyncio.run(
            process_requests_async(
                llm,
                prompts,
                sampling_params,
                args,
                max_concurrent=args.max_concurrent
            )
        )

        # Calculate throughput metrics
        total_time = stats["end_time"] - stats["start_time"]
        total_tokens = stats["total_prompt_tokens"] + stats["total_output_tokens"]
        throughput_tokens_per_sec = total_tokens / total_time if total_time > 0 else 0
        throughput_requests_per_sec = stats["completed"] / total_time if total_time > 0 else 0

        def _percentile(values, pct):
            if not values:
                return 0
            s = sorted(values)
            idx = min(int(len(s) * pct), len(s) - 1)
            return s[idx]

        avg_latency = sum(stats["latencies"]) / len(stats["latencies"]) if stats["latencies"] else 0
        p50_latency = _percentile(stats["latencies"], 0.50)
        p95_latency = _percentile(stats["latencies"], 0.95)
        p99_latency = _percentile(stats["latencies"], 0.99)

        avg_ttft = sum(stats["ttfts"]) / len(stats["ttfts"]) if stats["ttfts"] else 0
        p50_ttft = _percentile(stats["ttfts"], 0.50)
        p95_ttft = _percentile(stats["ttfts"], 0.95)
        p99_ttft = _percentile(stats["ttfts"], 0.99)

        avg_tpot = sum(stats["tpots"]) / len(stats["tpots"]) if stats["tpots"] else 0
        p50_tpot = _percentile(stats["tpots"], 0.50)
        p95_tpot = _percentile(stats["tpots"], 0.95)
        p99_tpot = _percentile(stats["tpots"], 0.99)

        # Print summary
        print("=" * 70)
        print(f"Throughput Benchmark Results for {args.algo}")
        print("=" * 70)
        print(f"Total requests processed:     {stats['completed']}")
        print(f"Total time:                   {total_time:.2f} seconds")
        print(f"Total prompt tokens:          {stats['total_prompt_tokens']}")
        print(f"Total output tokens:          {stats['total_output_tokens']}")
        print(f"Total tokens:                 {total_tokens}")
        print("-" * 70)
        print(f"Throughput (tokens/sec):      {throughput_tokens_per_sec:.2f}")
        print(f"Throughput (requests/sec):    {throughput_requests_per_sec:.2f}")
        print("-" * 70)
        print(f"Average latency:              {avg_latency * 1000:.2f} ms")
        print(f"P50 latency:                  {p50_latency * 1000:.2f} ms")
        print(f"P95 latency:                  {p95_latency * 1000:.2f} ms")
        print(f"P99 latency:                  {p99_latency * 1000:.2f} ms")
        print("-" * 70)
        print(f"Average TTFT:                 {avg_ttft * 1000:.2f} ms  (n={len(stats['ttfts'])})")
        print(f"P50 TTFT:                     {p50_ttft * 1000:.2f} ms")
        print(f"P95 TTFT:                     {p95_ttft * 1000:.2f} ms")
        print(f"P99 TTFT:                     {p99_ttft * 1000:.2f} ms")
        print("-" * 70)
        print(f"Average TPOT:                 {avg_tpot * 1000:.2f} ms  (n={len(stats['tpots'])})")
        print(f"P50 TPOT:                     {p50_tpot * 1000:.2f} ms")
        print(f"P95 TPOT:                     {p95_tpot * 1000:.2f} ms")
        print(f"P99 TPOT:                     {p99_tpot * 1000:.2f} ms")
        print("=" * 70)

        # Save results if requested
        if args.output_file:
            output_data = {
                "algorithm": args.algo,
                "cache_size": cache_size,
                "block_size": args.block_size,
                "max_concurrent": args.max_concurrent,
                "output_length": args.output_length,
                "prefill_only": args.prefill_only,
                "trace_file": args.trace_file,
                "metrics": {
                    "total_requests": stats["completed"],
                    "total_time_seconds": total_time,
                    "total_prompt_tokens": stats["total_prompt_tokens"],
                    "total_output_tokens": stats["total_output_tokens"],
                    "total_tokens": total_tokens,
                    "throughput_tokens_per_sec": throughput_tokens_per_sec,
                    "throughput_requests_per_sec": throughput_requests_per_sec,
                    "avg_latency_ms": avg_latency * 1000,
                    "p50_latency_ms": p50_latency * 1000,
                    "p95_latency_ms": p95_latency * 1000,
                    "p99_latency_ms": p99_latency * 1000,
                    "avg_ttft_ms": avg_ttft * 1000,
                    "p50_ttft_ms": p50_ttft * 1000,
                    "p95_ttft_ms": p95_ttft * 1000,
                    "p99_ttft_ms": p99_ttft * 1000,
                    "ttft_sample_count": len(stats["ttfts"]),
                    "avg_tpot_ms": avg_tpot * 1000,
                    "p50_tpot_ms": p50_tpot * 1000,
                    "p95_tpot_ms": p95_tpot * 1000,
                    "p99_tpot_ms": p99_tpot * 1000,
                    "tpot_sample_count": len(stats["tpots"]),
                },
                "latencies": stats["latencies"],
                "ttfts": stats["ttfts"],
                "tpots": stats["tpots"],
            }

            with open(args.output_file, 'w') as f:
                json.dump(output_data, f, indent=2)
            print(f"Results saved to {args.output_file}")

    finally:
        pass


if __name__ == "__main__":
    main()
