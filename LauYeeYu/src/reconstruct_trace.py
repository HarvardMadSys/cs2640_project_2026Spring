import json
import argparse
from collections import defaultdict

def reconstruct_trace(trace_file, log_file):
    """
    Reconstructs the hit/miss sequence for a trace based on a log of kept reuse intervals.

    :param trace_file: Path to the original JSONL trace file.
    :param log_file: Path to the log file containing kept reuse intervals.
    """
    print(f"Loading kept intervals from {log_file}...")
    kept_intervals = set()
    with open(log_file, 'r') as f:
        for line in f:
            interval = json.loads(line)
            # A kept interval is identified by the block and when its reuse period started
            kept_intervals.add((interval['block_id'], interval['t_start']))
    print(f"Loaded {len(kept_intervals)} kept intervals.")

    print(f"Processing trace file {trace_file} to reconstruct hits and misses...")
    
    last_access = {} # To store the last access time of each block_id
    total_requests = 0
    total_hits = 0
    total_misses = 0

    with open(trace_file, 'r') as f:
        for t, line in enumerate(f):
            req = json.loads(line)['hash_ids']
            
            for block_id in req:
                total_requests += 1
                if block_id in last_access:
                    t_last = last_access[block_id]
                    reuse_distance = t - t_last
                    
                    # Check if the interval starting from the last access is a "kept" one
                    if (block_id, t_last) in kept_intervals:
                        print(f"Hit: {block_id}, reuse distance {reuse_distance}")
                        total_hits += 1
                    else:
                        print(f"Miss: {block_id}, reuse distance {reuse_distance}")
                        total_misses += 1
                else:
                    # First access is always a miss
                    print(f"Miss: {block_id}, reuse distance -1")
                    total_misses += 1
                
                # Update the last access time for this block
                last_access[block_id] = t

    print("\n--- Reconstruction Summary ---")
    print(f"Total blocks processed: {total_requests}")
    print(f"Total Hits: {total_hits}")
    print(f"Total Misses: {total_misses}")
    if total_requests > 0:
        hit_rate = (total_hits / total_requests) * 100
        print(f"Hit Rate: {hit_rate:.2f}%")
    print("----------------------------")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reconstruct trace hits and misses from an ILP solution log.")
    parser.add_argument('--trace-file', type=str, required=True,
                        help='Path to the original JSONL trace file.')
    parser.add_argument('--log-file', type=str, required=True,
                        help='Path to the log file with kept reuse intervals from ilp_reuse_interval.py.')
    
    args = parser.parse_args()

    reconstruct_trace(args.trace_file, args.log_file)
