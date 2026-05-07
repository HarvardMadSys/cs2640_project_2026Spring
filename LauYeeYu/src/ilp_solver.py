
import json
from collections import defaultdict
import gurobipy as gp
from gurobipy import GRB
import argparse
import time

def solve_dynamic_prefix_caching_ilp(trace_file, cache_capacity, max_requests=None, threads=None):
    """
    Formulates and solves the DYNAMIC prefix caching problem as an Integer Linear Program.

    This model enforces a "full sequence residence" policy: after a request is
    processed, all of its blocks must be in the cache. The optimizer decides which
    *other* blocks to keep in the remaining space to maximize future hits.

    :param trace_file: Path to the JSONL file containing request traces.
    :param cache_capacity: The total capacity of the cache.
    :param max_requests: The maximum number of requests to process from the trace file.
    :param threads: The number of threads for Gurobi to use.
    """
    print("Loading trace file...")
    initial_requests = []
    with open(trace_file, 'r') as f:
        for i, line in enumerate(f):
            if max_requests and i >= max_requests:
                break
            initial_requests.append(json.loads(line)['hash_ids'])
    
    # --- Preprocessing Step ---
    # Filter out requests that are impossible to cache entirely.
    requests = [req for req in initial_requests if len(set(req)) <= cache_capacity]
    print(f"Filtered requests: {len(requests)} of {len(initial_requests)} remain after removing those larger than cache capacity.")

    num_requests = len(requests)
    print(f"Processing {num_requests} requests...")

    # --- Model Creation ---
    m = gp.Model("dynamic_prefix_caching")

    # --- Gurobi Parameter Tuning ---
    if threads is not None:
        m.setParam('Threads', threads)
        print(f"Gurobi thread limit set to: {threads}")

    # --- Variable Pruning and Creation ---
    print("Creating variables with pruning...")
    x_vars_to_create = []
    all_unique_blocks = set(b for req in requests for b in req)
    seen_blocks = set()
    req_sets = [set(req) for req in requests]

    for t in range(num_requests + 1):
        if t > 0:
            seen_blocks.update(req_sets[t-1])
        for b in seen_blocks:
            x_vars_to_create.append((b, t))
    
    x = m.addVars(x_vars_to_create, vtype=GRB.BINARY, name="x")

    # --- Objective Function ---
    # Maximize total compute saving based on what's in the cache BEFORE the request.
    objective_expr = gp.quicksum(
        (i + 1) * x[block_id, t]
        for t, req in enumerate(requests)
        for i, block_id in enumerate(req)
        if (block_id, t) in x
    )
    m.setObjective(objective_expr, GRB.MAXIMIZE)

    # --- Constraints ---
    print("Building constraints...")

    for t in range(num_requests):
        # 1. Cache Capacity: Must be met at every time step (including the next one).
        m.addConstr(gp.quicksum(x.select('*', t)) <= cache_capacity, f"capacity_{t}")
        m.addConstr(gp.quicksum(x.select('*', t + 1)) <= cache_capacity, f"capacity_{t+1}")

        # 2. State Transition with Full Residence Constraint:
        req_set = req_sets[t]
        for b in all_unique_blocks:
            # We only create constraints for variables that can exist.
            if (b, t + 1) not in x:
                continue
            
            if b in req_set:
                # For blocks in the current request, they MUST be in the cache at t+1.
                m.addConstr(x[b, t + 1] == 1, name=f"residence_{b}_{t}")
            else:
                # For other blocks, they can only stay if they were already present at t.
                # The optimizer chooses whether to keep them (x=1) or evict (x=0).
                if (b, t) in x:
                    m.addConstr(x[b, t + 1] <= x[b, t], name=f"carryover_{b}_{t}")
                else:
                    # If it wasn't seen before t and isn't in req t, it can't be in cache at t+1
                    m.addConstr(x[b, t + 1] == 0, name=f"non-existent_{b}_{t}")

    # --- Model Size Estimation ---
    m.update()
    print("\n--- Model Size ---")
    print(f"Number of variables: {m.NumVars} (after pruning)")
    print(f"Number of constraints: {m.NumConstrs}")
    print("--------------------\n")

    # Optimize the model
    print("Starting optimization...")
    start_time = time.time()
    m.optimize()
    end_time = time.time()
    print(f"Optimization finished in {end_time - start_time:.2f} seconds.")

    # --- Print Results ---
    if m.status == GRB.OPTIMAL:
        print(f"\nOptimal objective value (total compute saving): {m.objVal}")
        for t in range(num_requests):
            req = requests[t]
            cached_at_t = sorted([b for b, time in x.keys() if time == t and x[b, t].x > 0.5])
            hit_indices = [i for i, block_id in enumerate(req) if (block_id, t) in x and x[block_id, t].x > 0.5]

            print(f"\nTime {t}: Request = {req}")
            print(f"  - Cache content before request ({len(cached_at_t)} blocks): {cached_at_t}")
            print(f"  - Hit indices in request: {hit_indices}")
            
            cached_after_t = sorted([b for b, time in x.keys() if time == t + 1 and x[b, t + 1].x > 0.5])
            print(f"  - Cache content after request ({len(cached_after_t)} blocks): {cached_after_t}")
    else:
        print("No optimal solution found.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Solve the DYNAMIC full-residence caching problem using ILP.")
    parser.add_argument('trace_file', type=str, 
                        help='Path to the JSONL trace file.')
    parser.add_argument('--cache-size', type=int, default=100,
                        help='Cache capacity in number of blocks.')
    parser.add_argument('--max-requests', type=int, default=10,
                        help='Limit the number of requests to process. WARNING: a high number will be very slow.')
    parser.add_argument('--threads', type=int, default=None,
                        help='Limit the number of threads Gurobi uses.')
    
    args = parser.parse_args()
    solve_dynamic_prefix_caching_ilp(args.trace_file, args.cache_size, args.max_requests, args.threads)
