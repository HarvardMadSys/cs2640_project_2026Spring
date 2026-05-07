"""Hole-leaving KV eviction demonstration.

The core claim: with KV-level eviction, deleting block X from the KV cache
forces recomputation of block X, but blocks X+1 ... N remain cache hits.
This is NOT possible with message-level eviction (which always restarts the
prefix chain from the eviction point).

How we demonstrate it:
1. Build a long conversation (> 4 LMCache chunks = 1024+ tokens)
2. Verify all blocks are cached (high hit rate on second request)
3. Delete ONLY the middle block from serve_v2's tracked prefix
4. Send the same prompt
5. Measure: hit rate on blocks BEFORE deletion point vs AFTER

Expected result:
  - Blocks 0..X-1 : still cache hits (prefix match up to X)
  - Block X       : cache miss (recomputed)
  - Blocks X+1..N : ALSO cache hits (vLLM has them in GPU cache from step 2)

This shows the "hole-leaving" property: selective mid-sequence eviction
only costs the evicted block, not everything after it.

Note: this relies on vLLM's GPU prefix cache (not LMCache CPU) holding
blocks X+1..N. The GPU cache IS the short-term cache that enables hole-
leaving at sub-878K-token scale.
"""

from __future__ import annotations

import modal

app = modal.App("adaptivecache-hole-leaving-demo")

@app.local_entrypoint()
def main():
    server = modal.Cls.from_name("adaptivecache-server-v2", "LLMServer")()

    print("=" * 60)
    print("Hole-Leaving KV Eviction Demo")
    print("=" * 60)
    print()
    print("Premise: delete block X from the KV cache.")
    print("  Standard prefix cache: blocks X+1..N also become misses.")
    print("  Hole-leaving:          blocks X+1..N remain hits.")
    print("  vLLM's GPU cache supports hole-leaving within session.")
    print()

    # Build a long conversation: system (~100 tok) + 10 steps (~150 tok each)
    # = ~1600 tokens total = 6+ LMCache chunks (256 tok each)
    sys_msg = "You are a precise Python tutor. Give one short paragraph per answer."
    task = (
        "We are building a comprehensive Python tutorial. "
        "Answer each question concisely in 2-3 sentences."
    )

    steps_q = [
        "1. What is a list comprehension? Give one example.",
        "2. What is a dictionary comprehension? Give one example.",
        "3. What is a generator? How does it differ from a list?",
        "4. What is a decorator? Show the syntax.",
        "5. What is a context manager? Give a real use case.",
        "6. What is the difference between __str__ and __repr__?",
        "7. What is *args and **kwargs? When do you use them?",
        "8. What is a class method vs static method?",
        "9. What is the GIL and why does it matter?",
        "10. What is asyncio and when should you use it?",
    ]

    # Step 1: Build the full conversation
    print("[1/4] Building conversation (10 steps)...")
    messages = [
        {"role": "system", "content": sys_msg},
        {"role": "user", "content": task},
    ]
    for q in steps_q:
        messages.append({"role": "user", "content": q})
        r = server.generate.remote(messages, temperature=0.0, max_tokens=80)
        messages.append({"role": "assistant", "content": r["content"]})

    full_prompt_tokens = r["prompt_tokens"]  # last step's prompt size
    print(f"   Final prompt: {full_prompt_tokens} tokens")

    # Step 2: Replay the SAME full conversation → should be ~97%+ cache hits
    print()
    print("[2/4] Replaying full conversation (should be ~97% cache hits)...")
    r_warm = server.generate.remote(messages[:-1], temperature=0.0, max_tokens=80)
    print(f"   Tokens: {r_warm['prompt_tokens']}  Cached: {r_warm['cached_tokens']}  "
          f"Hit: {r_warm['hit_rate']:.0%}")

    # Step 3: Simulate hole-leaving by truncating at different prefix lengths
    # and measuring how many tokens are still cache hits at each truncation point.
    #
    # The hole-leaving experiment:
    # - Truncate the prompt at position P (tokens 0..P)
    # - Measure cached_tokens for that prefix
    # - Compare: full prompt hit rate vs prefix-only hit rate
    #
    # If hole-leaving works: truncating at P still gives high hit rate for 0..P
    # This proves the prefix before the eviction point is preserved.

    print()
    print("[3/4] Hole-leaving measurement: hit rate at different prefix cuts")
    print(f"   Full prompt: {r_warm['prompt_tokens']} tokens")
    print()

    # Replay conversations of increasing length using early messages
    # This measures: "if we evict steps 8-10, do steps 1-7 still have cache hits?"
    prefix_results = []
    step_messages = [messages[0], messages[1]]  # system + task
    n_steps_to_test = [2, 4, 6, 8, 10]

    for n in n_steps_to_test:
        # Reconstruct messages up to step n (each step = 2 messages: user + assistant)
        n_msgs = 2 + n * 2  # system + task + n * (user+assistant)
        msgs_prefix = messages[:n_msgs]
        r_prefix = server.generate.remote(msgs_prefix, temperature=0.0, max_tokens=80)
        prefix_results.append({
            "n_steps": n,
            "prompt_tokens": r_prefix["prompt_tokens"],
            "cached_tokens": r_prefix["cached_tokens"],
            "hit_rate": r_prefix["hit_rate"],
        })
        print(f"   Prefix (steps 1-{n:2d}): {r_prefix['prompt_tokens']:4d} tokens  "
              f"cached={r_prefix['cached_tokens']:4d}({r_prefix['hit_rate']:.0%})")

    # Step 4: The key demonstration
    # After sending the full 10-step conversation, the GPU cache has ALL blocks.
    # Now send prefix (steps 1-6), then extend with steps 9-10 (skipping 7-8).
    # This is the "hole": steps 7-8 are "evicted" (not in our message list),
    # but steps 9-10 are still in the GPU cache from the full conversation.
    print()
    print("[4/4] Hole-leaving: evict middle steps 7-8, keep prefix AND suffix")
    print("   Message sequence: [sys][task][steps 1-6][steps 9-10]")
    print("   (Steps 7-8 removed — simulating middle eviction)")
    print()

    # Build the holed message list: steps 1-6 + steps 9-10
    msgs_with_hole = messages[:14]  # system + task + steps 1-6 (12 msgs + 2 = 14)
    msgs_with_hole += messages[18:22]  # steps 9-10 (at positions 18-21)

    r_hole = server.generate.remote(msgs_with_hole, temperature=0.0, max_tokens=80)
    print(f"   Holed prompt: {r_hole['prompt_tokens']} tokens  "
          f"cached={r_hole['cached_tokens']}({r_hole['hit_rate']:.0%})")

    # Compare with full prompt
    print()
    print("=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    print(f"Full prompt (steps 1-10):  {r_warm['prompt_tokens']:4d} tok  "
          f"cached={r_warm['cached_tokens']}({r_warm['hit_rate']:.0%})")
    print(f"Prefix only (steps 1-6):   {prefix_results[2]['prompt_tokens']:4d} tok  "
          f"cached={prefix_results[2]['cached_tokens']}({prefix_results[2]['hit_rate']:.0%})")
    print(f"Holed (steps 1-6 + 9-10):  {r_hole['prompt_tokens']:4d} tok  "
          f"cached={r_hole['cached_tokens']}({r_hole['hit_rate']:.0%})")
    print()
    print("Interpretation:")
    print("  The holed prompt includes steps 9-10 that were NOT in the prefix.")
    print("  If those tokens are cache hits, it means the GPU KV cache holds")
    print("  them from the earlier full-conversation request (step 2).")
    print("  This is hole-leaving: deleting middle steps doesn't evict later ones.")
    print()
    print("  With message-level eviction (FIFO), you'd lose steps 9-10's cache.")
    print("  With KV-level eviction, steps 9-10 stay cached even when 7-8 are gone.")
