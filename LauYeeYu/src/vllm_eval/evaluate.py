import argparse
import json
import math
import sys
import logging
import random
import time
from tqdm import tqdm

from vllm.v1.core.block_pool import BlockPool
import vllm.v1.core.free_block_manager as free_block_manager
from vllm.v1.core.kv_cache_utils import make_block_hash_with_group_id
from vllm.v1.core.single_type_kv_cache_manager import FullAttentionManager
from vllm.v1.kv_cache_interface import FullAttentionSpec

logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

def get_bin_for_intensity(intensity: int) -> int:
    if intensity < 0: return -1
    if intensity == 0: return 0
    if intensity == 1: return 1
    return int(math.floor(math.log2(intensity))) + 1

def get_range_for_bin(bin_index: int):
    if bin_index == 0: return (0, 0)
    if bin_index == 1: return (1, 1)
    start = 1 << (bin_index - 1)
    end = (1 << bin_index) - 1
    return (start, end)

def parse_cache_size(size_str: str) -> int:
    size_str = size_str.lower()
    if size_str.endswith('g'):
        return int(float(size_str[:-1]) * 1024 * 1024 * 1024)
    if size_str.endswith('m'):
        return int(float(size_str[:-1]) * 1024 * 1024)
    if size_str.endswith('k'):
        return int(float(size_str[:-1]) * 1024)
    return int(size_str)

def precompute_next_access_times(trace_file: str, request_limit: int = None):
    """Pre-compute the next access time for each block hash.

    Returns a dictionary mapping (request_idx, block_hash) -> next_access_time
    where next_access_time is in terms of global block count.
    """
    # First pass: build access history
    block_accesses = {}  # block_hash -> list of (request_idx, block_time)
    request_count = 0
    global_block_time = 0

    with open(trace_file, 'r') as f:
        for line in f:
            if request_limit is not None and request_count >= request_limit:
                break

            line = line.strip()
            if not line:
                continue

            data = json.loads(line)
            hash_ids = data.get("hash_ids", [])

            if not hash_ids:
                continue

            for block_idx, h in enumerate(hash_ids):
                if h not in block_accesses:
                    block_accesses[h] = []
                block_accesses[h].append((request_count, global_block_time))
                global_block_time += 1

            request_count += 1

    # Second pass: compute next access times
    next_access_times = {}  # (request_idx, block_hash) -> next_access_time

    for block_hash, accesses in block_accesses.items():
        for i in range(len(accesses)):
            req_idx, block_time = accesses[i]
            # Find the next access time
            if i + 1 < len(accesses):
                next_req_idx, next_block_time = accesses[i + 1]
                next_access_times[(req_idx, block_hash)] = next_block_time
            else:
                # No future access - use infinity
                next_access_times[(req_idx, block_hash)] = float('inf')

    return next_access_times

def main():
    # Set random seed using current time
    seed = int(time.time() * 1000) % (2**32)
    random.seed(seed)
    print(f"Random seed set to: {seed}")

    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-file", required=True)
    parser.add_argument("--cache-size", required=True)
    parser.add_argument("--align-with-libcachesim", action="store_true")
    parser.add_argument("--algo", type=str, default="LCD", help="Algorithm for free block manager (e.g., LCD, LRU)")
    parser.add_argument("--dump-segments", type=str, default=None, help="Output JSONL file for dumping computed and missed segments")
    parser.add_argument("--request-limit", type=int, default=None, help="Stop after this many requests mapped from trace")
    parser.add_argument("--latency-file", type=str, default=None, help="JSON file containing realistic latencies mapped to calculate block compute intensity")
    parser.add_argument("--compute-intensity-file", type=str, default=None, help="JSON file (same fitted-curve schema as --latency-file) used to monkey-patch free_block_manager.get_compute_intensity, so eviction algorithms see realistic per-block compute cost (also used for the saved-compute ratio).")
    parser.add_argument("--profile-file", type=str, default=None, help="Per-trace IAT/LAG profile JSON (used by Formula* algorithms to set one-hit defaults)")
    parser.add_argument("--default-percentile", type=str, default="0.95", choices=["0.5", "0.9", "0.95", "0.99"], help="Quantile to use from --profile-file for IAT/LAG defaults")
    parser.add_argument("--dump-denominator-trace", type=str, default=None, help="Output JSONL of (current_time, denominator) pairs from adaptive managers' _denominator_trace.")
    parser.add_argument("--dump-ratio-trace", type=str, default=None, help="Output JSONL of (current_time, small_ratio) pairs from RandomSmallQueueAdaptive*'s _ratio_trace.")
    args = parser.parse_args()

    cache_size = parse_cache_size(args.cache_size)
    trace_file = args.trace_file

    dump_segments_file = None
    if args.dump_segments:
        dump_segments_file = open(args.dump_segments, 'w')

    if args.compute_intensity_file:
        with open(args.compute_intensity_file, 'r') as f:
            ci_data = json.load(f)
        ci_block_size = ci_data.get("args", {}).get("block_size", 16)
        a = ci_data["fitted_curve"]["a"]
        b = ci_data["fitted_curve"]["b"]

        def patched_get_compute_intensity(index: int) -> float:
            seq_len = (index + 0.5) * ci_block_size
            derivative = 2 * a * seq_len + b
            return float(1000.0 * derivative * ci_block_size)

        free_block_manager.get_compute_intensity = patched_get_compute_intensity
        print(f"Patched get_compute_intensity from {args.compute_intensity_file} (a={a:.3e}, b={b:.3e}, idx=0->{patched_get_compute_intensity(0):.4f}ms, idx=100->{patched_get_compute_intensity(100):.4f}ms)")

    compute_intensity_fn = free_block_manager.get_compute_intensity

    # Optionally load latency-based cost function for TTFT output
    ttft_intensity_fn = None
    if args.latency_file:
        with open(args.latency_file, 'r') as f:
            latency_data = json.load(f)
            real_block_size = latency_data.get("args", {}).get("block_size", 16)
            a = latency_data["fitted_curve"]["a"]
            b = latency_data["fitted_curve"]["b"]

            def ttft_intensity_fn(index: int) -> float:
                seq_len = (index + 0.5) * real_block_size
                derivative = 2 * a * seq_len + b
                return float(1000.0 * derivative * real_block_size)

            print(f"Using realistic latency scale for TTFT calculation (a={a:.3e}, b={b:.3e}, idx=0->{ttft_intensity_fn(0):.4f}ms, idx=100->{ttft_intensity_fn(100):.4f}ms).")

    # Initialize mock environment
    # 1. BlockPool
    pool = BlockPool(num_gpu_blocks=cache_size, enable_caching=True, hash_block_size=1)

    # 2. Bind FreeBlockManager
    manager_cls_name = f"{args.algo}FreeBlockManager"
    if hasattr(free_block_manager, manager_cls_name):
        manager_cls = getattr(free_block_manager, manager_cls_name)
    else:
        raise ValueError(f"Unknown algorithm class {manager_cls_name} in vllm.v1.core.free_block_manager")
    
    # Inject per-trace IAT/LAG defaults for Formula* managers.
    if args.algo.startswith("Formula") and args.profile_file is not None:
        with open(args.profile_file, 'r') as pf:
            profile_data = json.load(pf)
        if trace_file in profile_data:
            entry = profile_data[trace_file]
            pct = args.default_percentile
            iat_q = entry["iat"]["quantiles"]
            lag_q = entry["last_access_gap"]["quantiles"]
            iat_def = float(iat_q.get(pct, iat_q.get(str(pct), 0.0)))
            lag_def = float(lag_q.get(pct, lag_q.get(str(pct), 0.0)))
            lag_p99 = float(lag_q.get("0.99", 0.0))
            from vllm.v1.core.free_block_manager import FormulaFreeBlockManager
            FormulaFreeBlockManager.IAT_DEFAULT = iat_def
            FormulaFreeBlockManager.LAG_DEFAULT = lag_def
            FormulaFreeBlockManager.LAG_P99_DEFAULT = lag_p99
            print(f"Formula defaults set from profile (P{pct}): IAT={iat_def:.0f}, LAG={lag_def:.0f}, LAG_P99={lag_p99:.0f}")
        else:
            print(f"WARN: trace {trace_file} not found in profile, using built-in defaults")

    pool.free_block_manager = manager_cls(pool.blocks)
    pool.null_block = pool.free_block_manager.get_free_blocks_n(1)[0]
    pool.null_block.is_null = True

    # If Belady based algorithms are selected, precompute next access times
    next_access_times_lookup = None
    if args.algo.startswith("Belady"):
        print(f"Precomputing next access times for {args.algo} algorithm...")
        next_access_times_lookup = precompute_next_access_times(trace_file, args.request_limit)
        print(f"Precomputed {len(next_access_times_lookup)} next access times")

    # 3. KVCacheSpec for match_prefix_cache
    import torch
    spec = FullAttentionSpec(
        block_size=1,
        num_kv_heads=1,
        head_size=1,
        dtype=torch.float16,
    )

    num_bins = 32
    actual_compute = 0.0
    saved_compute = 0.0
    total_compute_bins = [0.0] * num_bins
    saved_compute_bins = [0.0] * num_bins

    # TTFT accumulators (only used when latency file is provided)
    actual_ttft = 0.0
    saved_ttft = 0.0
    total_ttft_bins = [0.0] * num_bins
    saved_ttft_bins = [0.0] * num_bins

    request_count = 0
    total_blocks = 0
    total_hits = 0

    with open(trace_file, 'r') as f:
        total_lines = sum(1 for ln in f if ln.strip())
    if args.request_limit is not None:
        total_lines = min(total_lines, args.request_limit)

    with open(trace_file, 'r') as f:
        for line in tqdm(f, total=total_lines, desc=f"Evaluating {args.algo}", unit="req"):
            if args.request_limit is not None and request_count >= args.request_limit:
                break

            line = line.strip()
            if not line:
                continue

            data = json.loads(line)
            hash_ids = data.get("hash_ids", [])

            if not hash_ids:
                continue

            # vLLM `record_request_blocks` will map these to `BlockHash`. Just
            # use 8-byte big-endian bytes representation of the integers.
            # Use int.from_bytes(block_hash[:8], 'big') to convert back.
            hash_bytes = [h.to_bytes(8, "big", signed=False) for h in hash_ids]

            # Ensure Space (null block reserves one slot, so usable is cache_size - 1)
            if len(hash_ids) >= cache_size:
                continue

            if args.align_with_libcachesim:
                if hasattr(pool.free_block_manager, "unhashed_blocks_queue"):
                    unhashed_count = pool.free_block_manager.unhashed_blocks_queue.num_free_blocks
                    needed = len(hash_ids) - unhashed_count
                    if needed > 0:
                        evicted_blocks = []
                        
                        blocks = pool.free_block_manager._try_get_free_blocks_from_free_radix_tree_nodes(needed)
                        evicted_blocks.extend(blocks)
                        needed -= len(blocks)
                        
                        if needed > 0:
                            blocks = pool.free_block_manager._try_get_free_blocks_from_in_use_radix_tree_nodes(needed)
                            evicted_blocks.extend(blocks)
                            needed -= len(blocks)
                            
                        for b in evicted_blocks:
                            pool._maybe_evict_cached_block(b)
                        
                        pool.free_block_manager.unhashed_blocks_queue.append_n(evicted_blocks)
                        pool.free_block_manager._num_free_blocks += len(evicted_blocks)

            request_count += 1

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

            if dump_segments_file is not None:
                computed_segments = []
                if hit_indices:
                    sorted_hits = sorted(list(hit_indices))
                    start = sorted_hits[0]
                    prev = start
                    for idx in sorted_hits[1:]:
                        if idx == prev + 1:
                            prev = idx
                        else:
                            computed_segments.append([start, prev + 1])
                            start = idx
                            prev = idx
                    computed_segments.append([start, prev + 1])
                
                missed_segments = []
                miss_indices = [i for i in range(len(hash_ids)) if i not in hit_indices]
                if miss_indices:
                    start = miss_indices[0]
                    prev = start
                    for idx in miss_indices[1:]:
                        if idx == prev + 1:
                            prev = idx
                        else:
                            missed_segments.append([start, prev + 1])
                            start = idx
                            prev = idx
                    missed_segments.append([start, prev + 1])
                
                dump_data = {
                    "request_id": request_count,
                    "total_blocks": len(hash_ids),
                    "computed_segments": computed_segments,
                    "recompute_segments": missed_segments
                }
                dump_segments_file.write(json.dumps(dump_data) + "\n")

            # In parser.h, compute_cost (req->features[0]) is `i+1` where i is the position index.
            # Next request checks hit/miss.

            # evaluate.cpp loops over the requests and for each block:
            # `if (cache->get(...) == false)` -> miss -> actual_compute += cost
            # `else` -> hit -> saved_compute += cost

            total_blocks += len(hash_ids)
            total_hits += len(hit_indices)

            for i, h in enumerate(hash_ids):
                cost = compute_intensity_fn(i)
                bin_idx = get_bin_for_intensity(i + 1)

                if bin_idx >= 0 and bin_idx < num_bins:
                    total_compute_bins[bin_idx] += cost

                if i in hit_indices:
                    saved_compute += cost
                    if bin_idx >= 0 and bin_idx < num_bins:
                        saved_compute_bins[bin_idx] += cost
                else:
                    actual_compute += cost

                if ttft_intensity_fn is not None:
                    ttft_cost = ttft_intensity_fn(i)
                    if bin_idx >= 0 and bin_idx < num_bins:
                        total_ttft_bins[bin_idx] += ttft_cost
                    if i in hit_indices:
                        saved_ttft += ttft_cost
                        if bin_idx >= 0 and bin_idx < num_bins:
                            saved_ttft_bins[bin_idx] += ttft_cost
                    else:
                        actual_ttft += ttft_cost

            # Manage blocks
            if hit_blocks:
                pool.touch(hit_blocks)

            request_blocks = [None] * len(hash_ids)
            # Add hit blocks at their original indices
            for idx, blk in zip(hit_indices_list, hit_blocks):
                request_blocks[idx] = blk

            miss_indices = [i for i in range(len(hash_ids)) if i not in hit_indices]
            if miss_indices:
                missed_segments = []
                start = miss_indices[0]
                prev = start
                for idx in miss_indices[1:]:
                    if idx == prev + 1:
                        prev = idx
                    else:
                        missed_segments.append((start, prev + 1))
                        start = idx
                        prev = idx
                missed_segments.append((start, prev + 1))

                for seg_start, seg_end in missed_segments:
                    seg_len = seg_end - seg_start
                    try:
                        new_blocks = pool.get_new_blocks(seg_len)
                    except ValueError as e:
                        logger.error(f"Failed to get new blocks. Need {seg_len}, have {pool.get_num_free_blocks()} free.")
                        raise

                    for i in range(seg_len):
                        idx = seg_start + i
                        blk = new_blocks[i]
                        bh = make_block_hash_with_group_id(hash_bytes[idx], 0)
                        blk.block_hash = bh
                        pool.cached_block_hash_to_block.insert(bh, blk)
                        request_blocks[idx] = blk

            # For Belady based algorithms, update next access times before recording the request
            if args.algo.startswith("Belady") and next_access_times_lookup is not None:
                for i, h in enumerate(hash_ids):
                    key = (request_count - 1, h)  # request_count was already incremented
                    if key in next_access_times_lookup:
                        bh = make_block_hash_with_group_id(hash_bytes[i], 0)
                        pool.free_block_manager.next_access_times[bh] = next_access_times_lookup[key]

            # Request finishes, free blocks
            pool.free_blocks(request_blocks, record_request=True)

            # Checks every 1000 requests
            if request_count % 1000 == 0:
                available_free = pool.get_num_free_blocks()
                assert available_free == cache_size - 1, f"Expected {cache_size - 1} free blocks, got {available_free}"
                if hasattr(pool.free_block_manager, "radix_tree"):
                    pool.free_block_manager.radix_tree._check_radix_tree_sanity()

    if actual_compute == 0.0:
        logger.info("No actual compute recorded.")
        return

    ratio = saved_compute / float(actual_compute + saved_compute)
    hit_rate = total_hits / float(total_blocks) if total_blocks > 0 else 0.0
    print(f"Algorithm {args.algo}: Saved compute: {saved_compute:.4f}, Actual compute: {actual_compute:.4f}, Ratio: {ratio:.3f}")
    print(f"Algorithm {args.algo}: Raw hit rate: {total_hits}/{total_blocks} = {hit_rate:.4f}")

    print(f"Algorithm {args.algo}: Compute savings by intensity bin:")
    for i in range(num_bins):
        if total_compute_bins[i] > 0.0:
            rg = get_range_for_bin(i)
            r = saved_compute_bins[i] / float(total_compute_bins[i])
            if rg[0] == rg[1]:
                print(f"  Bin {i + 1:2d} (Intensity {rg[0]:4d}):       Saved {saved_compute_bins[i]:12.4f} / Total {total_compute_bins[i]:12.4f} (Ratio: {r:.3f})")
            else:
                print(f"  Bin {i + 1:2d} (Range {rg[0]:7d} - {rg[1]:<7d}): Saved {saved_compute_bins[i]:12.4f} / Total {total_compute_bins[i]:12.4f} (Ratio: {r:.3f})")

    if ttft_intensity_fn is not None and (actual_ttft + saved_ttft) > 0.0:
        ttft_ratio = saved_ttft / float(actual_ttft + saved_ttft)
        print(f"Algorithm {args.algo}: Saved TTFT: {saved_ttft:.4f}, Actual TTFT: {actual_ttft:.4f}, TTFT Ratio: {ttft_ratio:.3f}")

        print(f"Algorithm {args.algo}: TTFT savings by intensity bin:")
        for i in range(num_bins):
            if total_ttft_bins[i] > 0.0:
                rg = get_range_for_bin(i)
                r = saved_ttft_bins[i] / float(total_ttft_bins[i])
                if rg[0] == rg[1]:
                    print(f"  Bin {i + 1:2d} (Intensity {rg[0]:4d}):       Saved {saved_ttft_bins[i]:12.4f} / Total {total_ttft_bins[i]:12.4f} (Ratio: {r:.3f})")
                else:
                    print(f"  Bin {i + 1:2d} (Range {rg[0]:7d} - {rg[1]:<7d}): Saved {saved_ttft_bins[i]:12.4f} / Total {total_ttft_bins[i]:12.4f} (Ratio: {r:.3f})")

    if dump_segments_file:
        dump_segments_file.close()

    if args.dump_denominator_trace:
        trace = getattr(pool.free_block_manager, "_denominator_trace", None)
        if trace is None:
            print(f"WARN: manager {args.algo} has no _denominator_trace attribute")
        else:
            with open(args.dump_denominator_trace, "w") as f:
                for t, d in trace:
                    f.write(json.dumps({"t": t, "d": d}) + "\n")
            print(f"Wrote {len(trace)} denominator samples to {args.dump_denominator_trace}")

    if args.dump_ratio_trace:
        trace = getattr(pool.free_block_manager, "_ratio_trace", None)
        if trace is None:
            print(f"WARN: manager {args.algo} has no _ratio_trace attribute")
        else:
            with open(args.dump_ratio_trace, "w") as f:
                for t, r in trace:
                    f.write(json.dumps({"t": t, "r": r}) + "\n")
            print(f"Wrote {len(trace)} ratio samples to {args.dump_ratio_trace}")

if __name__ == "__main__":
    main()
