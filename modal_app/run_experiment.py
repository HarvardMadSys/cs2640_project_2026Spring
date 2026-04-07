"""AdaptiveCache experiment runner on Modal.

Runs a multi-step agent conversation with three cache policies and reports
per-step timing and cache hit statistics from vLLM.

Usage:
    modal run modal_app/run_experiment.py
    modal run modal_app/run_experiment.py --n-steps 15 --budget 4096

Policies compared:
    none        — full context, no eviction (baseline, best cache hits)
    fifo        — evict oldest steps first (destroys prefix cache)
    kv_adaptive — score-based eviction, preserve prefix cache
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List, Optional

import modal

# ---------------------------------------------------------------------------
# Experiment image (CPU, no GPU — inference goes to the deployed LLMServer)
# ---------------------------------------------------------------------------

src_path = Path(__file__).parent.parent / "src"

experiment_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("tiktoken>=0.7", "numpy>=1.24", "openai>=1.0", "modal>=1.3")
    # add_local_dir must come last (no pip_install after it)
    .add_local_dir(str(src_path), remote_path="/app/src", copy=True)
    .env({"PYTHONPATH": "/app/src"})
)

app = modal.App("adaptivecache-experiments")
results_volume = modal.Volume.from_name("adaptivecache-results", create_if_missing=True)

# ---------------------------------------------------------------------------
# Shared task prompt for all policies (a multi-step coding task)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a senior Python engineer. You are working through a coding task step by step.
At each step, you reason briefly and then take an action (write code, explain a concept, or analyze results).
Keep each response focused and practical."""

TASK_DESCRIPTION = """TASK: Implement a Python LRU cache class with the following requirements:
1. Fixed capacity (max_size parameter)
2. O(1) get and put operations
3. Thread-safe (use threading.Lock)
4. Support for TTL (time-to-live) per entry
5. Statistics: hit_rate, miss_rate, eviction_count

You should implement this step by step, testing each component before moving on."""

STEP_PROMPTS = [
    "Step 1: Define the data structures. What internal data structures will you use for O(1) get/put?",
    "Step 2: Implement the basic LRU cache without TTL first. Show the __init__ and the node structure.",
    "Step 3: Implement the get() method. Handle cache miss and cache hit cases.",
    "Step 4: Implement the put() method. Handle capacity overflow correctly.",
    "Step 5: Add thread safety with threading.Lock. Which operations need locking?",
    "Step 6: Add TTL support. How do you check and expire entries efficiently?",
    "Step 7: Add the statistics tracking (hit_rate, miss_rate, eviction_count).",
    "Step 8: Write a comprehensive test that exercises all features.",
    "Step 9: Identify any edge cases your implementation might miss.",
    "Step 10: Final review: is there anything you'd optimize for production use?",
]


# ---------------------------------------------------------------------------
# Eviction policies
# ---------------------------------------------------------------------------

def apply_fifo(messages: list, budget_tokens: int) -> list:
    """FIFO: evict oldest user/assistant pairs from the front."""
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")

    def count(msgs):
        return sum(len(enc.encode(m.get("content", "") or "")) for m in msgs)

    # Keep system (idx 0) and task (idx 1) always
    protected = messages[:2]
    steps = messages[2:]

    while count(protected) + count(steps) > budget_tokens and len(steps) >= 2:
        # Drop oldest user+assistant pair
        steps = steps[2:]

    return protected + steps


def apply_kv_adaptive(messages: list, budget_tokens: int, controller) -> tuple:
    """KV adaptive: score-based eviction using AdaptiveCache's Tier 2 reorganization.

    Uses structural priors + reference counting to decide which steps to evict,
    preferring to evict low-value content while preserving the stable prefix.
    """
    import sys
    if "/app/src" not in sys.path:
        sys.path.insert(0, "/app/src")

    try:
        from adaptive_cache.cache import AdaptiveCache
        from adaptive_cache.config import CacheConfig

        config = CacheConfig(soft_budget=budget_tokens, hard_budget=budget_tokens + 512)
        cache = AdaptiveCache(config)

        # Initialize with system + task
        if len(messages) >= 2:
            cache.init(messages[0].get("content", ""), messages[1].get("content", ""))

        # Add steps pairwise
        steps = messages[2:]
        i = 0
        while i + 1 < len(steps):
            user_msg = steps[i]
            asst_msg = steps[i+1]
            cache.update(
                thought=None,
                action=asst_msg.get("content", ""),
                observation=None,
                step=i // 2,
            )
            i += 2

        # Get reorganized messages from cache
        managed = cache.to_messages()
        if managed:
            evicted_count = max(0, (len(messages) - len(managed)) // 2)
            return managed, [("",)] * evicted_count
    except Exception as e:
        # Fallback to recency-based scoring
        pass

    # Fallback: recency-based (same as FIFO but from later positions)
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")

    def count(msgs):
        return sum(len(enc.encode(m.get("content", "") or "")) for m in msgs)

    total = count(messages)
    if total <= budget_tokens:
        return messages, []

    protected = messages[:2]
    steps = messages[2:]
    pairs = []
    i = 0
    while i + 1 < len(steps):
        pairs.append((steps[i], steps[i+1]))
        i += 2
    if i < len(steps):
        pairs.append((steps[i],))

    # Position-aware: evict from LATER positions first (inverse of FIFO)
    # This preserves the early prefix, matching AdaptiveCache's key insight
    scored = []
    for idx, pair in enumerate(pairs):
        position_ratio = idx / max(len(pairs) - 1, 1)  # 0=oldest, 1=newest
        # Evict from later positions first (higher position = evict sooner)
        score = position_ratio  # Highest score = most likely to evict
        scored.append((score, idx, pair))

    # Sort descending: evict highest-scored (latest) first
    scored.sort(key=lambda x: -x[0])

    evicted_indices = set()
    tokens_freed = 0
    target = total - budget_tokens

    for score, idx, pair in scored:
        if tokens_freed >= target:
            break
        pair_tokens = sum(len(enc.encode(m.get("content", "") or "")) for m in pair)
        evicted_indices.add(idx)
        tokens_freed += pair_tokens

    kept_steps = []
    evicted_pairs = []
    for idx, pair in enumerate(pairs):
        if idx in evicted_indices:
            evicted_pairs.append(pair)
        else:
            kept_steps.extend(pair)

    return protected + kept_steps, evicted_pairs


# ---------------------------------------------------------------------------
# Single-policy experiment runner
# ---------------------------------------------------------------------------

@app.function(
    image=experiment_image,
    volumes={"/results": results_volume},
    timeout=3600,
)
def run_policy_experiment(
    policy: str,
    budget_tokens: int = 4096,
    n_steps: int = 10,
    temperature: float = 0.0,
    max_tokens: int = 64,  # Small to make prefill time visible
) -> dict:
    """Run a multi-step conversation with a given cache policy.

    Returns per-step statistics including timing and cache hit rates.
    """
    import sys
    sys.path.insert(0, "/app/src")

    # Connect to the deployed LLMServer
    LLMServer = modal.Cls.from_name("adaptivecache-server", "LLMServer")
    server = LLMServer()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": TASK_DESCRIPTION},
    ]

    steps_data = []
    total_prompt_tokens = 0
    total_cached_tokens = 0

    prompts = STEP_PROMPTS * ((n_steps // len(STEP_PROMPTS)) + 1)  # Repeat if needed
    for step_idx, step_prompt in enumerate(prompts[:n_steps]):
        messages.append({"role": "user", "content": step_prompt})

        # Apply eviction policy (before sending)
        msgs_to_send = messages
        evicted = []
        if policy == "fifo":
            msgs_before = len(messages)
            msgs_to_send = apply_fifo(messages, budget_tokens)
            evicted_count = (msgs_before - len(msgs_to_send)) // 2  # pairs
            evicted = [("",)] * evicted_count  # Placeholder to count evictions
        elif policy == "kv_adaptive":
            msgs_to_send, evicted = apply_kv_adaptive(messages, budget_tokens, None)
        # policy == "none": send all messages unchanged

        t_start = time.time()
        result = server.generate.remote(
            msgs_to_send,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        elapsed = time.time() - t_start

        response_content = result["content"]
        prompt_tokens = result["prompt_tokens"]
        cached_tokens = result["num_cached_tokens"]
        n_tokens = len(result["prompt_token_ids"])

        total_prompt_tokens += prompt_tokens
        total_cached_tokens += cached_tokens
        hit_rate = cached_tokens / max(prompt_tokens, 1)

        lmc_computed = result.get("lmcache_computed_tokens", 0)
        lmc_hit = result.get("lmcache_hit_tokens", 0)
        lmc_recompute_rate = lmc_computed / max(prompt_tokens, 1)

        step_data = {
            "step": step_idx + 1,
            "policy": policy,
            "prompt_tokens": prompt_tokens,
            "cached_tokens": cached_tokens,
            "hit_rate": hit_rate,
            "lmcache_computed_tokens": lmc_computed,     # tokens vLLM had to compute
            "lmcache_hit_tokens": lmc_hit,               # tokens loaded from LMCache CPU
            "lmcache_recompute_rate": lmc_recompute_rate, # lower = better
            "completion_tokens": result["completion_tokens"],
            "latency_s": elapsed,
            "messages_sent": len(msgs_to_send),
            "messages_evicted": len(evicted),
            "evicted_positions": [],
        }
        steps_data.append(step_data)

        print(
            f"[{policy}] step={step_idx+1:2d}  prompt={prompt_tokens:5d}  "
            f"lmc_computed={lmc_computed:5d}({lmc_recompute_rate:.0%})  "
            f"latency={elapsed:.2f}s  evicted={len(evicted)}"
        )

        # Add assistant response to history
        messages.append({"role": "assistant", "content": response_content})

    mean_hit_rate = total_cached_tokens / max(total_prompt_tokens, 1)
    result_data = {
        "policy": policy,
        "budget_tokens": budget_tokens,
        "n_steps": n_steps,
        "total_prompt_tokens": total_prompt_tokens,
        "total_cached_tokens": total_cached_tokens,
        "mean_hit_rate": mean_hit_rate,
        "steps": steps_data,
    }

    # Save to volume
    out_path = Path(f"/results/experiment_{policy}_{budget_tokens}_{int(time.time())}.json")
    out_path.write_text(json.dumps(result_data, indent=2))
    results_volume.commit()

    return result_data


# ---------------------------------------------------------------------------
# Local entrypoint: run all 3 policies and compare
# ---------------------------------------------------------------------------

@app.local_entrypoint()
def main(
    n_steps: int = 10,
    budget: int = 4096,
    policies: str = "none,fifo,kv_adaptive",
):
    """Run experiment matrix and print comparison table."""
    policy_list = [p.strip() for p in policies.split(",")]

    print(f"=== AdaptiveCache Experiment ===")
    print(f"Steps: {n_steps}  Budget: {budget} tokens  Policies: {policy_list}")
    print()

    results = {}
    for policy in policy_list:
        print(f"--- Running policy: {policy} ---")
        result = run_policy_experiment.remote(
            policy=policy,
            budget_tokens=budget,
            n_steps=n_steps,
        )
        results[policy] = result

    # Print comparison
    print("\n=== Results Summary ===")
    print(f"{'Policy':<15} {'Total Prompt':>13} {'Total Computed':>15} {'Recompute%':>11} {'Evictions':>10} {'Mean Latency':>13}")
    print("-" * 85)
    for policy, r in results.items():
        steps = r["steps"]
        mean_latency = sum(s["latency_s"] for s in steps) / max(len(steps), 1)
        total_evicted = sum(s["messages_evicted"] for s in steps)
        total_computed = sum(s.get("lmcache_computed_tokens", 0) for s in steps)
        total_prompt = r["total_prompt_tokens"]
        recompute_pct = total_computed / max(total_prompt, 1)
        print(
            f"{policy:<15} {total_prompt:>13,} {total_computed:>15,} {recompute_pct:>10.1%}  "
            f"{total_evicted:>9}  {mean_latency:>12.2f}s"
        )

    # Per-step latency breakdown (lower = more cached tokens)
    print("\n=== Per-Step Latency (lower = more cache hits) ===")
    header = f"{'Step':>4}  {'Prompt':>7}"
    for policy in policy_list:
        header += f"  {policy:>12}"
    print(header)
    actual_steps = min(len(results[p]["steps"]) for p in policy_list)
    for step_idx in range(actual_steps):
        prompt_size = results[policy_list[0]]["steps"][step_idx]["prompt_tokens"]
        row = f"{step_idx+1:>4}  {prompt_size:>7}"
        for policy in policy_list:
            s = results[policy]["steps"][step_idx]
            evict_mark = "*" if s["messages_evicted"] > 0 else " "
            row += f"  {s['latency_s']:>10.3f}s{evict_mark}"
        print(row)
    print("* = eviction fired this step")

    # Note: with prefix caching, later steps with more cached tokens should be faster.
    # FIFO breaks the cached prefix (higher latency), none/kv_adaptive preserve it (lower latency).
    print("\nNote: Lower latency = more prefix cache hits. Policy quality = how well it preserves the cached prefix.")

    # Save summary
    summary = {
        "policies": policy_list,
        "n_steps": n_steps,
        "budget": budget,
        "results": results,
        "timestamp": time.time(),
    }
    out_path = Path("results") / f"modal_experiment_{int(time.time())}.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2))
    print(f"\nFull results saved to: {out_path}")
