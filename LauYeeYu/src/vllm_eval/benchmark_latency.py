import argparse
import json
import math
import sys
import logging
import time
from tqdm import tqdm

from vllm.v1.core.block_pool import BlockPool
import vllm.v1.core.free_block_manager as free_block_manager
from vllm.v1.core.kv_cache_utils import make_block_hash_with_group_id
from vllm.v1.core.single_type_kv_cache_manager import FullAttentionManager
from vllm.v1.kv_cache_interface import FullAttentionSpec

logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

def parse_cache_size(size_str: str) -> int:
    size_str = size_str.lower()
    if size_str.endswith('g'):
        return int(float(size_str[:-1]) * 1024 * 1024 * 1024)
    if size_str.endswith('m'):
        return int(float(size_str[:-1]) * 1024 * 1024)
    if size_str.endswith('k'):
        return int(float(size_str[:-1]) * 1024)
    return int(size_str)


def run_simulation(args, cache_size, trace_file):
    # Initialize mock environment
    # 1. BlockPool
    pool = BlockPool(
        num_gpu_blocks=cache_size,
        enable_caching=True,
        hash_block_size=args.block_size,
        block_manager_algo=args.algo
    )

    # null_block initialization is still needed for simulated environment
    if hasattr(pool, 'free_block_manager') and hasattr(pool.free_block_manager, 'get_free_blocks_n'):
        pool.null_block = pool.free_block_manager.get_free_blocks_n(1)[0]
        pool.null_block.is_null = True
    else:
        raise ValueError(f"Unknown algorithm class {args.algo}FreeBlockManager or missing get_free_blocks_n")

    # 3. KVCacheSpec for match_prefix_cache
    import torch
    spec = FullAttentionSpec(
        block_size=args.block_size,
        num_kv_heads=1,
        head_size=1,
        dtype=torch.float16,
    )

    request_count = 0
    total_latency_time = 0.0
    latencies = []

    print(f"Starting simulated benchmark for algo {args.algo} with cache size {cache_size} and block size {args.block_size}")

    with open(trace_file, 'r') as f:
        for line in tqdm(f, total=args.request_limit, desc=f"Simulating {args.algo}"):
            if args.request_limit is not None and request_count >= args.request_limit:
                break

            line = line.strip()
            if not line:
                continue

            data = json.loads(line)
            hash_ids = data.get("hash_ids", [])

            if not hash_ids:
                continue

            # Ensure Space (skip if req needs more blocks than total cache size)
            if len(hash_ids) > cache_size - 1:
                continue

            # Synthesize token content based on hash IDs
            # Two blocks with different prefix will have different hash IDs in the trace,
            # we map each hash ID to unique content (token IDs).
            synthesized_token_ids = []
            for h in hash_ids:
                # We use modulo to ensure the token ID is within a reasonable range (e.g. vocab size)
                token_id = h % 128000
                synthesized_token_ids.extend([token_id] * args.block_size)

            request_count += 1

            start_time = time.perf_counter()

            # vLLM `record_request_blocks` will map these to `BlockHash`.
            # We construct `hash_bytes` using 8-byte big-endian representation.
            hash_bytes = [h.to_bytes(8, "big", signed=False) for h in hash_ids]

            # match_prefix_cache
            segments = FullAttentionManager.match_prefix_cache(
                block_hashes=hash_bytes,
                max_length=len(hash_bytes),
                kv_cache_group_ids=[0],
                block_pool=pool,
                kv_cache_spec=spec,
                use_eagle=False,
                alignment_tokens=1
            )

            hit_indices = set()
            hit_indices_list = []
            hit_blocks = []

            for seg in segments:
                for offset in range(seg.length_in_blocks):
                    idx = seg.start_block_index + offset
                    hit_indices.add(idx)
                    hit_indices_list.append(idx)
                    hit_blocks.append(seg.blocks.blocks[0][offset])

            # Manage blocks
            if hit_blocks:
                pool.touch(hit_blocks)

            miss_count = len(hash_ids) - len(hit_blocks)
            miss_blocks = []
            if miss_count > 0:
                try:
                    miss_blocks = pool.get_new_blocks(miss_count)
                except ValueError as e:
                    logger.error(f"Failed to get new blocks. Need {miss_count}, have {pool.get_num_free_blocks()} free.")
                    raise

            request_blocks = [None] * len(hash_ids)
            # Add hit blocks at their original indices
            for idx, blk in zip(hit_indices_list, hit_blocks):
                request_blocks[idx] = blk

            # Add new miss blocks in the remaining gaps
            miss_idx = 0
            for i in range(len(hash_ids)):
                if request_blocks[i] is None:
                    blk = miss_blocks[miss_idx]
                    miss_idx += 1
                    bh = make_block_hash_with_group_id(hash_bytes[i], 0)
                    blk.block_hash = bh
                    pool.cached_block_hash_to_block.insert(bh, blk)
                    request_blocks[i] = blk

            # Request finishes, free blocks
            pool.free_blocks(request_blocks)

            end_time = time.perf_counter()
            req_latency = end_time - start_time
            latencies.append(req_latency)
            total_latency_time += req_latency

    # Calculate percentile latencies
    avg_latency = (total_latency_time / request_count) if request_count > 0 else 0.0
    sorted_latencies = sorted(latencies)
    p50_latency = sorted_latencies[len(sorted_latencies) // 2] if sorted_latencies else 0.0
    p95_latency = sorted_latencies[int(len(sorted_latencies) * 0.95)] if sorted_latencies else 0.0
    p99_latency = sorted_latencies[int(len(sorted_latencies) * 0.99)] if sorted_latencies else 0.0

    # Print summary
    print("=" * 70)
    print(f"Latency Benchmark Results for {args.algo} (Simulation)")
    print("=" * 70)
    print(f"Total requests processed:     {request_count}")
    print(f"Total time:                   {total_latency_time:.4f} seconds")
    print("-" * 70)
    print(f"Average latency:              {avg_latency * 1000:.4f} ms")
    print(f"P50 latency:                  {p50_latency * 1000:.4f} ms")
    print(f"P95 latency:                  {p95_latency * 1000:.4f} ms")
    print(f"P99 latency:                  {p99_latency * 1000:.4f} ms")
    print("=" * 70)

    # Save structured output if output-file is specified
    if args.output_file:
        output_data = {
            "algorithm": args.algo,
            "mode": "simulation",
            "cache_size": cache_size,
            "block_size": args.block_size,
            "metrics": {
                "total_requests": request_count,
                "total_time_seconds": total_latency_time,
                "avg_latency_ms": avg_latency * 1000,
                "p50_latency_ms": p50_latency * 1000,
                "p95_latency_ms": p95_latency * 1000,
                "p99_latency_ms": p99_latency * 1000,
            },
            "latencies": latencies,
        }
        with open(args.output_file, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"Results saved to {args.output_file}")

    # Keep backward compatibility with --output-latency-file
    if args.output_latency_file:
        with open(args.output_latency_file, "w") as f:
            json.dump({
                "latencies": latencies,
                "average_latency": avg_latency
            }, f, indent=4)


async def run_real_model_async(args, cache_size, trace_file, model_id_or_path):
    """Async version using AsyncLLMEngine for comparison with benchmark_throughput.py"""
    import torch

    # Import after monkey patching (exactly like benchmark_throughput.py does)
    from vllm import AsyncLLMEngine, SamplingParams, AsyncEngineArgs

    # Create engine args (same as benchmark_throughput.py)
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

    print(f"Initializing AsyncLLMEngine with model={model_id_or_path}, backend={args.attention_backend}")
    llm = AsyncLLMEngine.from_engine_args(engine_args)

    # Get vocab size (this is tricky with async engine)
    vocab_size = 32000  # default fallback
    try:
        model_config = await llm.get_model_config()
        if hasattr(model_config, 'hf_config') and hasattr(model_config.hf_config, 'vocab_size'):
            vocab_size = model_config.hf_config.vocab_size
    except:
        vocab_size = 128000  # fallback for modern models

    sampling_params = SamplingParams(max_tokens=1, temperature=0, ignore_eos=True)

    print(f"Starting async real benchmark for algo {args.algo}")

    # Parse trace and build all token ids up-front
    all_prompt_token_ids = []
    with open(trace_file, 'r') as f:
        for line in f:
            if args.request_limit is not None and len(all_prompt_token_ids) >= args.request_limit:
                break

            line = line.strip()
            if not line:
                continue

            data = json.loads(line)
            hash_ids = data.get("hash_ids", [])
            input_length = data.get("input_length", None)

            if not hash_ids:
                continue

            if len(hash_ids) > cache_size - 1:
                continue

            synthesized_token_ids = []
            for h in hash_ids:
                current_seed = h
                for _ in range(args.block_size):
                    block_seed = current_seed % vocab_size
                    synthesized_token_ids.append(block_seed)
                    current_seed //= vocab_size

            if input_length is not None:
                synthesized_token_ids = synthesized_token_ids[:input_length]

            all_prompt_token_ids.append(synthesized_token_ids)

    request_count = len(all_prompt_token_ids)
    print(f"Prepared {request_count} requests.")

    total_latency_time = 0.0
    latencies = []

    # Process requests sequentially (like benchmark_throughput with max_concurrent=1)
    for i, prompt_token_ids in enumerate(tqdm(all_prompt_token_ids, desc=f"Async Real Model {args.algo}")):
        request_id = f"request-{i}"

        # Timing
        start = time.perf_counter()

        # Generate with async engine (iterate through stream like benchmark_throughput)
        final_output = None
        async for output in llm.generate(
            prompt={"prompt_token_ids": prompt_token_ids},
            sampling_params=sampling_params,
            request_id=request_id
        ):
            final_output = output

        end = time.perf_counter()

        latency = end - start

        if args.exclude_schedule_time and final_output:
            if final_output.metrics is not None and getattr(final_output.metrics, 'first_token_ts', 0) > 0 and getattr(final_output.metrics, 'scheduled_ts', 0) > 0:
                latency = final_output.metrics.first_token_ts - final_output.metrics.scheduled_ts

        latencies.append(latency)
        total_latency_time += latency

    # Calculate percentile latencies
    avg_latency = (total_latency_time / request_count) if request_count > 0 else 0.0
    sorted_latencies = sorted(latencies)
    p50_latency = sorted_latencies[len(sorted_latencies) // 2] if sorted_latencies else 0.0
    p95_latency = sorted_latencies[int(len(sorted_latencies) * 0.95)] if sorted_latencies else 0.0
    p99_latency = sorted_latencies[int(len(sorted_latencies) * 0.99)] if sorted_latencies else 0.0

    # Print summary
    print("=" * 70)
    print(f"Latency Benchmark Results for {args.algo} (Async Real Model)")
    print("=" * 70)
    print(f"Total requests processed:     {request_count}")
    print(f"Total time:                   {total_latency_time:.4f} seconds")
    print("-" * 70)
    print(f"Average latency:              {avg_latency * 1000:.4f} ms")
    print(f"P50 latency:                  {p50_latency * 1000:.4f} ms")
    print(f"P95 latency:                  {p95_latency * 1000:.4f} ms")
    print(f"P99 latency:                  {p99_latency * 1000:.4f} ms")
    print("=" * 70)

    # Save structured output if output-file is specified
    if args.output_file:
        output_data = {
            "algorithm": args.algo,
            "mode": "async_real_model",
            "cache_size": cache_size,
            "block_size": args.block_size,
            "metrics": {
                "total_requests": request_count,
                "total_time_seconds": total_latency_time,
                "avg_latency_ms": avg_latency * 1000,
                "p50_latency_ms": p50_latency * 1000,
                "p95_latency_ms": p95_latency * 1000,
                "p99_latency_ms": p99_latency * 1000,
            },
            "latencies": latencies,
        }
        with open(args.output_file, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"Results saved to {args.output_file}")

    # Keep backward compatibility with --output-latency-file
    if args.output_latency_file:
        with open(args.output_latency_file, "w") as f:
            json.dump({
                "latencies": latencies,
                "average_latency": avg_latency
            }, f, indent=4)


def run_real_model(args, cache_size, trace_file):
    import torch
    from vllm import LLM, SamplingParams
    import os

    # Support local file paths gracefully
    expanded_path = os.path.expanduser(args.model)
    model_id_or_path = os.path.abspath(expanded_path) if os.path.exists(expanded_path) else args.model

    # Check if async engine requested
    if args.use_async_engine:
        import asyncio
        print(f"Using AsyncLLMEngine for comparison with benchmark_throughput.py")
        asyncio.run(run_real_model_async(args, cache_size, trace_file, model_id_or_path))
        return

    print(f"Initializing real LLM with model={model_id_or_path}, backend={args.attention_backend}")
    llm = LLM(
        model=model_id_or_path,
        enable_prefix_caching=True,
        gpu_memory_utilization=0.9,
        tensor_parallel_size=1,
        max_model_len=args.max_model_len,
        trust_remote_code=True,
        attention_backend=args.attention_backend,
        num_gpu_blocks_override=cache_size,
        block_manager_algo=args.algo,
        max_num_batched_tokens=2048,
    )

    vocab_size = getattr(getattr(llm.llm_engine.model_config, "hf_config", None), "vocab_size", 32000)
    sampling_params = SamplingParams(max_tokens=1, temperature=0, ignore_eos=True)

    print(f"Starting real benchmark for algo {args.algo}")

    # Parse trace and build all token ids up-front
    all_prompt_token_ids = []
    with open(trace_file, 'r') as f:
        for line in f:
            if args.request_limit is not None and len(all_prompt_token_ids) >= args.request_limit:
                break

            line = line.strip()
            if not line:
                continue

            data = json.loads(line)
            hash_ids = data.get("hash_ids", [])
            input_length = data.get("input_length", None)

            if not hash_ids:
                continue

            if len(hash_ids) > cache_size - 1:
                continue

            synthesized_token_ids = []
            for h in hash_ids:
                current_seed = h
                for _ in range(args.block_size):
                    block_seed = (current_seed + 1) % vocab_size
                    synthesized_token_ids.append(block_seed)
                    current_seed //= vocab_size

            if input_length is not None:
                synthesized_token_ids = synthesized_token_ids[:input_length]

            all_prompt_token_ids.append(synthesized_token_ids)

    request_count = len(all_prompt_token_ids)
    print(f"Prepared {request_count} requests.")

    total_latency_time = 0.0
    latencies = []

    tokenizer = llm.get_tokenizer()

    for i, prompt_token_ids in enumerate(tqdm(all_prompt_token_ids, desc=f"Real Model {args.algo}")):
        prompts = [{"prompt_token_ids": prompt_token_ids}]

        # Timing exactly like benchmark_gap_filling.py
        torch.cuda.synchronize()
        start = time.perf_counter()
        outputs = llm.generate(
            prompts=prompts,
            sampling_params=sampling_params,
            use_tqdm=False
        )
        torch.cuda.synchronize()
        end = time.perf_counter()

        latency = end - start

        output = outputs[0]
        if args.exclude_schedule_time:
            if output.metrics is not None and getattr(output.metrics, 'first_token_ts', 0) > 0 and getattr(output.metrics, 'scheduled_ts', 0) > 0:
                # metrics.first_token_ts and metrics.scheduled_ts are monotonic EngineCore timestamps
                # difference is exactly prefill computation time (excluding scheduling/matching logic overhead)
                latency = output.metrics.first_token_ts - output.metrics.scheduled_ts
            else:
                assert False, "Output metrics are missing or invalid"
        else:
            latency = end - start

        latencies.append(latency)
        total_latency_time += latency

        output = outputs[0]
        num_cached = getattr(output, 'num_cached_tokens', 0) or 0
        prompt_len = len(prompt_token_ids)
        # print(f" Request {i+1}: {prompt_len} prompt tokens | "
        #       f"Cached: {num_cached} | Computed: {prompt_len - num_cached} | "
        #       f"Latency: {latency:.4f}s")

    # Calculate percentile latencies
    avg_latency = (total_latency_time / request_count) if request_count > 0 else 0.0
    sorted_latencies = sorted(latencies)
    p50_latency = sorted_latencies[len(sorted_latencies) // 2] if sorted_latencies else 0.0
    p95_latency = sorted_latencies[int(len(sorted_latencies) * 0.95)] if sorted_latencies else 0.0
    p99_latency = sorted_latencies[int(len(sorted_latencies) * 0.99)] if sorted_latencies else 0.0

    # Print summary
    print("=" * 70)
    print(f"Latency Benchmark Results for {args.algo} (Real Model)")
    print("=" * 70)
    print(f"Total requests processed:     {request_count}")
    print(f"Total time:                   {total_latency_time:.4f} seconds")
    print("-" * 70)
    print(f"Average latency:              {avg_latency * 1000:.4f} ms")
    print(f"P50 latency:                  {p50_latency * 1000:.4f} ms")
    print(f"P95 latency:                  {p95_latency * 1000:.4f} ms")
    print(f"P99 latency:                  {p99_latency * 1000:.4f} ms")
    print("=" * 70)

    # Save structured output if output-file is specified
    if args.output_file:
        output_data = {
            "algorithm": args.algo,
            "mode": "real_model",
            "cache_size": cache_size,
            "block_size": args.block_size,
            "metrics": {
                "total_requests": request_count,
                "total_time_seconds": total_latency_time,
                "avg_latency_ms": avg_latency * 1000,
                "p50_latency_ms": p50_latency * 1000,
                "p95_latency_ms": p95_latency * 1000,
                "p99_latency_ms": p99_latency * 1000,
            },
            "latencies": latencies,
        }
        with open(args.output_file, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"Results saved to {args.output_file}")

    # Keep backward compatibility with --output-latency-file
    if args.output_latency_file:
        with open(args.output_latency_file, "w") as f:
            json.dump({
                "latencies": latencies,
                "average_latency": avg_latency
            }, f, indent=4)


def main():
    import random
    import numpy as np
    import torch

    # Set random seed using current time
    seed = int(time.time() * 1000) % (2**32)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    print(f"Random seed set to: {seed}")

    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-file", required=True)
    parser.add_argument("--cache-size", required=True)
    parser.add_argument("--block-size", type=int, default=16, help="Block size (default: 16)")
    parser.add_argument("--request-limit", type=int, default=None, help="Stop after this many requests mapped from trace")
    parser.add_argument("--algo", type=str, default="LCD", help="Algorithm for free block manager (e.g., LCD, LRU)")
    parser.add_argument("--model", type=str, default=None, help="Hugging Face model ID to use for real benchmarking. If not provided, runs simulation.")
    parser.add_argument("--max-model-len", type=int, default=220000, help="Max model length for real engine.")
    parser.add_argument("--attention-backend", type=str, default="FLASHINFER", help="Attention backend (default: FLASHINFER)")
    parser.add_argument("--exclude-schedule-time", action="store_true", help="Exclude scheduling time from latency")
    parser.add_argument("--output-file", type=str, default=None, help="Output file for structured latency results (JSON)")
    parser.add_argument("--output-latency-file", type=str, default=None, help="(Deprecated) File to output the latency of every request")
    parser.add_argument("--latency-file", type=str, default=None, help="JSON file containing realistic latencies mapped to calculate block compute intensity")
    parser.add_argument("--use-async-engine", action="store_true", help="Use AsyncLLMEngine instead of synchronous LLM (for comparison with benchmark_throughput.py)")
    args = parser.parse_args()

    cache_size = parse_cache_size(args.cache_size)
    trace_file = args.trace_file

    if args.latency_file:
        with open(args.latency_file, 'r') as f:
            latency_data = json.load(f)
            # Fetch a and b for the quadratic formula specific to the block size profiling that curve fits to
            a = latency_data["fitted_curve"]["a"]
            b = latency_data["fitted_curve"]["b"]
            
            # Monkey-patch get_compute_intensity in free_block_manager
            # The seq_len that produced the latency = (index + 1) * args.block_size
            # The marginal additional cost per token produced at point L is roughly `2aL + b`
            # For a block of size block_size, the marginal cost is `block_size * (2aL + b)`
            # We scale this to reflect realistic relative compute intensity
            
            def patched_get_compute_intensity(index: int) -> float:
                # Use the middle of the block for a more accurate average latency of the tokens in the block
                seq_len = (index + 0.5) * args.block_size
                derivative = 2 * a * seq_len + b
                # You can also return 1000 * derivative * args.block_size if scale matters for density formulas
                # However, LCD primarily needs proportional comparisons. We return milliseconds.
                return float(1000.0 * derivative * args.block_size)
                
            free_block_manager.get_compute_intensity = patched_get_compute_intensity
            print(f"Monkey patched get_compute_intensity using realistic latency scale (a={a:.3e}, b={b:.3e}, idx=0->{patched_get_compute_intensity(0):.4f}ms, idx=100->{patched_get_compute_intensity(100):.4f}ms).")

    if args.model:
        run_real_model(args, cache_size, trace_file)
    else:
        run_simulation(args, cache_size, trace_file)

if __name__ == "__main__":
    main()
