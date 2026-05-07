import time
import argparse
import os
from typing import List, Optional, Tuple
import torch

from vllm import LLM, SamplingParams
from vllm.v1.request import Request
from vllm.v1.core.kv_cache_utils import make_block_hash_with_group_id

def parse_hit_segments(hit_segments_str: str) -> List[Tuple[int, int]]:
    """
    Parses a string like "0:10,20:5" into a list of tuples [(0, 10), (20, 5)].
    """
    segments = []
    if not hit_segments_str:
        return segments
    
    parts = hit_segments_str.split(',')
    for part in parts:
        start, length = map(int, part.split(':'))
        segments.append((start, length))
    return segments

def run_benchmark(
    model: str,
    num_prompt_blocks: int,
    hit_block_segments: str,
    tensor_parallel_size: int,
    max_model_len: Optional[int] = None,
    gpu_memory_utilization: float = 0.9,
    attention_backend: Optional[str] = None,
    batch_size: int = 1,
):
    print(f"Initializing LLM with model={model}")
    
    try:
        llm = LLM(
            model=model,
            enable_prefix_caching=True,
            max_model_len=max_model_len,
            tensor_parallel_size=tensor_parallel_size,
            enforce_eager=True,
            gpu_memory_utilization=gpu_memory_utilization,
            attention_backend=attention_backend,
        )
    except Exception as e:
        print(f"Failed to initialize LLM: {e}")
        return

    # Fetch block size from config
    block_size = llm.llm_engine.vllm_config.cache_config.block_size
    print(f"Block size: {block_size}")
    
    # Calculate token lengths
    prompt_len = num_prompt_blocks * block_size
    parsed_segments = parse_hit_segments(hit_block_segments)
    
    print(f"Configuration:")
    print(f"  Num Prompt Blocks: {num_prompt_blocks} ({prompt_len} tokens)")
    print(f"  Batch Size       : {batch_size}")
    print(f"  Hit Segments     : {parsed_segments}")
    
    if max_model_len and prompt_len > max_model_len:
         print(f"WARNING: prompt_len ({prompt_len}) > max_model_len ({max_model_len}). This will likely fail.")

    # Generate random prompt tokens
    tokenizer = llm.get_tokenizer()
    vocab_size = getattr(getattr(llm.llm_engine.model_config, "hf_config", None), "vocab_size", 32000)
    
    all_prompt_token_ids = []
    for _ in range(batch_size):
        ids = torch.randint(100, vocab_size - 100, (prompt_len,)).tolist()
        all_prompt_token_ids.append(ids)
    
    sampling_params = SamplingParams(max_tokens=1, temperature=0, ignore_eos=True)
    
    # --- 1. Warmup / Full Cache Population ---
    print(f"\n[Warmup] Running request to populate full cache...")
    
    # Prepare prompts for batch
    prompts = [{"prompt_token_ids": ids} for ids in all_prompt_token_ids]
    
    start = time.perf_counter()
    llm.generate(prompts=prompts, sampling_params=sampling_params, use_tqdm=True)
    end = time.perf_counter()
    print(f"[Warmup] Finished in {end - start:.4f}s")
    
    # Access internals
    try:
        client = llm.llm_engine.engine_core
        if hasattr(client, "engine_core"):
             engine_core = client.engine_core
        else:
             print("Warning: Could not access local engine_core. Are you running with VLLM_ENABLE_V1_MULTIPROCESSING=1?")
             return

        scheduler = engine_core.scheduler
        kv_cache_manager = scheduler.kv_cache_manager
        block_pool = kv_cache_manager.block_pool
        request_block_hasher = engine_core.request_block_hasher
    except AttributeError as e:
        print(f"Error accessing internals: {e}")
        return

    # Reconstruct request to get hashes
    all_block_hashes = []
    for i, ids in enumerate(all_prompt_token_ids):
        dummy_req = Request(
            request_id=f"hash_helper_{i}",
            prompt_token_ids=ids,
            sampling_params=sampling_params,
            pooling_params=None,
            eos_token_id=None,
            block_hasher=request_block_hasher
        )
        all_block_hashes.append(dummy_req.block_hashes)

    # --- 2. Full Prefix Hit (Baseline 1) ---
    print("\n[Benchmark] Full Prefix Hit...")
    # No eviction needed, cache is full from warmup
    torch.cuda.synchronize()
    start = time.perf_counter()
    llm.generate(prompts=prompts, sampling_params=sampling_params, use_tqdm=True)
    torch.cuda.synchronize()
    end = time.perf_counter()
    full_hit_time = end - start
    print(f"Full Prefix Hit Time: {full_hit_time:.4f}s")
    
    # --- 3. Partial Hit (Gap Filling) ---
    print(f"\n[Benchmark] Partial Hit (Segments: {hit_block_segments})...")
    
    blocks_to_evict = set()
    kept_count = 0
    for block_hashes in all_block_hashes:
        # Identify blocks to KEEP based on segments
        blocks_to_keep_indices = set()
        for start_idx, length in parsed_segments:
            for i in range(start_idx, start_idx + length):
                if i < len(block_hashes):
                    blocks_to_keep_indices.add(i)
        
        # Evict everything else
        for i, h in enumerate(block_hashes):
            if i not in blocks_to_keep_indices:
                key = make_block_hash_with_group_id(h, 0)
                block = block_pool.cached_block_hash_to_block.get_one_block(key)
                if block:
                    blocks_to_evict.add(block.block_id)
            else:
                kept_count += 1
    
    print(f"Evicting {len(blocks_to_evict)} blocks. Keeping {kept_count} blocks.")
    block_pool.evict_blocks(blocks_to_evict)
    
    torch.cuda.synchronize()
    start = time.perf_counter()
    llm.generate(prompts=prompts, sampling_params=sampling_params, use_tqdm=True)
    torch.cuda.synchronize()
    end = time.perf_counter()
    partial_hit_time = end - start
    print(f"Partial Hit Time: {partial_hit_time:.4f}s")

    # --- 3b. Prefix-Only Hit (Baseline 2: Just the prefix part) ---
    prefix_only_time = None
    prefix_segment = next((s for s in parsed_segments if s[0] == 0), None)
    
    if prefix_segment:
        print(f"\n[Benchmark] Prefix-Only Hit (Segment: {prefix_segment})...")
        
        # Identify blocks to KEEP (only the prefix)
        prefix_start, prefix_len = prefix_segment
        blocks_to_evict = set()
        kept_count = 0
        for block_hashes in all_block_hashes:
            blocks_to_keep_indices = set()
            for i in range(prefix_start, prefix_start + prefix_len):
                if i < len(block_hashes):
                    blocks_to_keep_indices.add(i)
            
            # Evict everything else
            for i, h in enumerate(block_hashes):
                if i not in blocks_to_keep_indices:
                    key = make_block_hash_with_group_id(h, 0)
                    block = block_pool.cached_block_hash_to_block.get_one_block(key)
                    if block:
                        blocks_to_evict.add(block.block_id)
                else:
                    kept_count += 1
        
        print(f"Evicting {len(blocks_to_evict)} blocks. Keeping {kept_count} blocks.")
        block_pool.evict_blocks(blocks_to_evict)
        
        torch.cuda.synchronize()
        start = time.perf_counter()
        llm.generate(prompts=prompts, sampling_params=sampling_params, use_tqdm=True)
        torch.cuda.synchronize()
        end = time.perf_counter()
        prefix_only_time = end - start
        print(f"Prefix-Only Hit Time: {prefix_only_time:.4f}s")
        
    # --- 3c. Equal-Length Prefix Hit (Baseline 3: Prefix with same total length) ---
    equal_len_hit_time = None
    total_hit_len = sum(length for _, length in parsed_segments)
    
    # Only run if it's different from the Prefix-Only Hit
    if total_hit_len > 0 and (not prefix_segment or prefix_segment[1] != total_hit_len):
        print(f"\n[Benchmark] Equal-Length Prefix Hit (Len: {total_hit_len})...")
        
        blocks_to_evict = set()
        kept_count = 0
        for block_hashes in all_block_hashes:
            # Identify blocks to KEEP (0 to total_hit_len)
            blocks_to_keep_indices = set()
            for i in range(total_hit_len):
                if i < len(block_hashes):
                    blocks_to_keep_indices.add(i)
            
            # Evict everything else
            for i, h in enumerate(block_hashes):
                if i not in blocks_to_keep_indices:
                    key = make_block_hash_with_group_id(h, 0)
                    block = block_pool.cached_block_hash_to_block.get_one_block(key)
                    if block:
                        blocks_to_evict.add(block.block_id)
                else:
                    kept_count += 1
        
        print(f"Evicting {len(blocks_to_evict)} blocks. Keeping {kept_count} blocks.")
        block_pool.evict_blocks(blocks_to_evict)
        
        torch.cuda.synchronize()
        start = time.perf_counter()
        llm.generate(prompts=prompts, sampling_params=sampling_params, use_tqdm=True)
        torch.cuda.synchronize()
        end = time.perf_counter()
        equal_len_hit_time = end - start
        print(f"Equal-Length Hit Time: {equal_len_hit_time:.4f}s")

    # --- 4. Non-Prefix Hit (Cold Baseline) ---
    print("\n[Benchmark] Non-Prefix Hit (Cold)...")
    print("Evicting ALL blocks...")
    block_pool.reset_prefix_cache()
    
    torch.cuda.synchronize()
    start = time.perf_counter()
    llm.generate(prompts=prompts, sampling_params=sampling_params, use_tqdm=True)
    torch.cuda.synchronize()
    end = time.perf_counter()
    cold_time = end - start
    print(f"Non-Prefix Hit Time: {cold_time:.4f}s")
    
    print("-" * 40)
    print(f"Summary Results (Lower is linear-better):")
    print(f"Full Hit (Best Case) : {full_hit_time:.4f}s")
    print(f"Partial Hit          : {partial_hit_time:.4f}s")
    if prefix_only_time is not None:
        print(f"Prefix-Only Hit      : {prefix_only_time:.4f}s")
    if equal_len_hit_time is not None:
        print(f"Equal-Length Hit     : {equal_len_hit_time:.4f}s")
    print(f"Cold Start (Worst)   : {cold_time:.4f}s")
    print("-" * 40)
    print(f"Relative to Best Case:")
    print(f"Partial Hit Overhead : {partial_hit_time / full_hit_time:.2f}x")
    if prefix_only_time is not None:
        print(f"Prefix-Only Overhead : {prefix_only_time / full_hit_time:.2f}x")
        print(f"Gap Cost (Partial-Prefix): {partial_hit_time - prefix_only_time:.4f}s")
    if equal_len_hit_time is not None:
        print(f"Equal-Length Overhead: {equal_len_hit_time / full_hit_time:.2f}x")
        print(f"Gap Cost (Partial-Equal) : {partial_hit_time - equal_len_hit_time:.4f}s")
    print(f"Cold Start Overhead  : {cold_time / full_hit_time:.2f}x")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, default="meta-llama/Llama-2-7b-chat-hf")
    parser.add_argument("--max-model-len", type=int, default=100000)
    parser.add_argument("--num-prompt-blocks", type=int, default=1024)
    parser.add_argument("--hit-block-segments", type=str, default="512:1", help="Comma-separated segments 'start:len,start:len' (e.g. '0:10,20:5')")
    parser.add_argument("--tensor-parallel-size", type=int, default=1)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.9)
    parser.add_argument("--attention-backend", type=str, default=None, help="Attention backend (e.g. FLASHINFER)")
    parser.add_argument("--batch-size", type=int, default=1, help="Number of concurrent requests")
    args = parser.parse_args()
    
    run_benchmark(
        model=args.model,
        num_prompt_blocks=args.num_prompt_blocks,
        hit_block_segments=args.hit_block_segments,
        tensor_parallel_size=args.tensor_parallel_size,
        max_model_len=args.max_model_len,
        gpu_memory_utilization=args.gpu_memory_utilization,
        attention_backend=args.attention_backend,
        batch_size=args.batch_size
    )
