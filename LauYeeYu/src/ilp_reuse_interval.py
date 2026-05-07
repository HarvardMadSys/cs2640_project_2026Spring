import json
from collections import defaultdict
import gurobipy as gp
from gurobipy import GRB
import argparse
import time

def compute_cost(index):
    """
    Compute the cost (FLOPS) for a cache miss at a given index.

    :param index: The position index in the request sequence.
    :return: The FLOPS cost.
    """
    return 865 + 2 * index


def solve_reuse_interval_ilp(trace_file, cache_capacity, max_requests=None, threads=None, log_file=None):
    """
    Formulates and solves the offline caching problem using a reuse-interval-based ILP.

    This model identifies all reuse intervals for each block and decides which
    intervals to "bridge" by keeping the block in the cache, to maximize compute savings.

    :param trace_file: Path to the JSONL file containing request traces.
    :param cache_capacity: The total capacity of the cache.
    :param max_requests: The maximum number of requests to process from the trace file.
    :param threads: The number of threads for Gurobi to use.
    :param log_file: Path to a file to log the kept reuse intervals.
    """
    print("Loading trace file and preprocessing to find reuse intervals...")
    requests = []
    skip_count = 0
    with open(trace_file, 'r') as f:
        for i, line in enumerate(f):
            if max_requests is not None and i >= max_requests:
                break
            req = json.loads(line)['hash_ids']
            if len(req) > cache_capacity:
                skip_count += 1
                continue
            requests.append(req)
    print(f"Loaded {len(requests)} requests from trace file. Skipped {skip_count} requests exceeding cache capacity.")
    
    num_requests = len(requests)

    # Step 1: Find all accesses for each block
    accesses = defaultdict(list)
    for t, req in enumerate(requests):
        for i, block_id in enumerate(req):
            accesses[block_id].append({'t': t, 'index': i})

    # Step 2: Generate reuse intervals from the access patterns
    reuse_intervals = []
    for block_id, access_list in accesses.items():
        for i in range(len(access_list) - 1):
            start_access = access_list[i]
            end_access = access_list[i+1]
            
            interval = {
                "block_id": block_id,
                "t_start": start_access['t'],
                "t_end": end_access['t'],
                "cost": compute_cost(end_access['index'])  # Cost of a miss is the saving we forgo (in FLOPS)
            }
            reuse_intervals.append(interval)
    
    print(f"Found {len(reuse_intervals)} reuse intervals from {num_requests} requests.")

    if not reuse_intervals:
        print("No reuse intervals found. Nothing to optimize.")
        return

    # --- Model Creation ---
    m = gp.Model("reuse_interval_caching")

    if threads is not None:
        m.setParam('Threads', threads)
        print(f"Gurobi thread limit set to: {threads}")

    # --- Decision Variables ---
    # x[i] = 1 if we keep the block for the i-th reuse interval, 0 otherwise.
    x = m.addVars(len(reuse_intervals), vtype=GRB.BINARY, name="x")

    # --- Objective Function ---
    # Maximize the total compute saving from bridging reuse intervals (i.e., getting hits).
    m.setObjective(gp.quicksum(reuse_intervals[i]["cost"] * x[i] for i in range(len(reuse_intervals))), GRB.MAXIMIZE)

    # --- Constraints ---
    # Capacity constraint: For each request, the number of blocks already in
    # the cache must not exceed the cache capacity minus the size of the
    # current request. This ensures there's enough space for the incoming request.
    
    # Optimization: Pre-calculate which intervals are live at each time step to avoid nested loops.
    print("Binning live intervals for each time step...")
    live_intervals_at_t = [[] for _ in range(num_requests)]
    for i, interval in enumerate(reuse_intervals):
        # An interval is live during [t_start, t_end), so we add its index to the bins for this range.
        for t in range(interval["t_start"], interval["t_end"]):
            # The constraint applies at time `t`, so we consider intervals active up to `t`.
            if t < num_requests:
                 live_intervals_at_t[t].append(i)

    print("Building capacity constraints for each time step...")
    for t, req in enumerate(requests):
        # Get the pre-computed list of interval indices that are live at time t.
        live_interval_indices = live_intervals_at_t[t]
        req_len = len(req)
        
        if live_interval_indices:
            # Select the corresponding decision variables.
            live_vars = [x[i] for i in live_interval_indices]
            m.addConstr(gp.quicksum(live_vars) <= cache_capacity - req_len, name=f"capacity_{t}")

    # --- Model Size Estimation ---
    m.update()
    print("\n--- Model Size ---")
    print(f"Number of variables: {m.NumVars}")
    print(f"Number of constraints: {m.NumConstrs}")
    print("--------------------")

    # -- Garbage Collection ---
    # These data structures are no longer needed for the ILP solve.
    import gc
    print("Cleaning up intermediate data structures before optimization...")
    del live_intervals_at_t
    del accesses
    del requests
    gc.collect()

    # --- Solve ---
    print("Starting optimization...")
    start_time = time.time()
    m.optimize()
    end_time = time.time()
    print(f"Optimization finished in {end_time - start_time:.2f} seconds.")

    # --- Print Results ---
    if m.status == GRB.OPTIMAL:
        print(f"\nOptimal objective value (total compute saving): {m.objVal}")
        hits_saved = 0
        total_possible_saving = sum(iv["cost"] for iv in reuse_intervals)
        
        kept_intervals_log = []
        for i in range(len(reuse_intervals)):
            if x[i].x > 0.5:
                hits_saved += 1
                if log_file:
                    kept_intervals_log.append(reuse_intervals[i])
        
        if log_file and kept_intervals_log:
            with open(log_file, 'w') as f:
                for interval in kept_intervals_log:
                    f.write(json.dumps(interval) + '\n')
            print(f"Logged {len(kept_intervals_log)} kept intervals to {log_file}")

        print(f"Total intervals bridged (hits secured): {hits_saved} out of {len(reuse_intervals)}")
        print(f"Total possible saving (if all misses avoided): {total_possible_saving}")
        
    else:
        print("No optimal solution found.")


def parse_cache_size(size_str):
    """
    Parse cache size from string. Supports both numeric values and shorthand notation.

    Examples:
        '1024' -> 1024
        '1k' -> 1024
        '10k' -> 10240
        '80k' -> 81920

    :param size_str: String representation of cache size.
    :return: Integer cache size in number of blocks.
    """
    size_str = str(size_str).strip().lower()

    if size_str.endswith('k'):
        # Remove 'k' and convert to blocks (multiply by 1024)
        base = size_str[:-1]
        try:
            return int(float(base) * 1024)
        except ValueError:
            raise argparse.ArgumentTypeError(f"Invalid cache size format: {size_str}")
    else:
        # Direct numeric value
        try:
            return int(size_str)
        except ValueError:
            raise argparse.ArgumentTypeError(f"Invalid cache size format: {size_str}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Solve the offline caching problem using reuse intervals.")
    parser.add_argument('trace_file', type=str,
                        help='Path to the JSONL trace file.')
    parser.add_argument('--cache-size', type=parse_cache_size, default=100,
                        help='Cache capacity in number of blocks. Supports numeric values (e.g., 1024) or shorthand (e.g., 1k, 10k).')
    parser.add_argument('--max-requests', type=int, default=None,
                        help='Limit the number of requests to process from the trace file. Default is no limit.')
    parser.add_argument('--threads', type=int, default=None,
                        help='Limit the number of threads Gurobi uses.')
    parser.add_argument('--log-file', type=str, default=None,
                        help='Path to log kept reuse intervals.')

    args = parser.parse_args()

    solve_reuse_interval_ilp(args.trace_file, args.cache_size, args.max_requests, args.threads, args.log_file)
