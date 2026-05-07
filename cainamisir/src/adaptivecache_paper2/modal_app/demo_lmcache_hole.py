"""LMCache hole-leaving demonstration.

Shows that deleting KV block X from LMCache's CPU store forces recomputation
of ONLY block X — blocks X+1..N are still served from LMCache CPU cache.

This is the key property that separates KV-level eviction from message-level
eviction: the prompt bytes never change, so the prefix chain is intact.

Setup: LMCACHE_SAVE_DECODE_CACHE=True forces LMCache to store ALL KV blocks
to CPU after each request (not just when GPU fills up). This lets us
demonstrate hole-leaving at any conversation length.

Run:
    modal run modal_app/demo_lmcache_hole.py
"""

from __future__ import annotations
import modal

app = modal.App("adaptivecache-lmcache-hole-demo")
CHUNK_SIZE = 256  # Must match LMCache chunk_size


def _parse_lmcache_log(text: str) -> dict:
    """Parse LMCache's per-request log line into a dict."""
    import re
    m = re.search(
        r"Reqid: (\d+), Total tokens (\d+), "
        r"Inference Engine computed tokens: (\d+), "
        r"LMCache hit tokens: (\d+), "
        r"need to load: (\d+)",
        text,
    )
    if not m:
        return {}
    return {
        "reqid": int(m.group(1)),
        "total_tokens": int(m.group(2)),
        "computed_tokens": int(m.group(3)),   # tokens vLLM had to compute
        "lmcache_hit_tokens": int(m.group(4)), # tokens from LMCache CPU
        "need_to_load": int(m.group(5)),
    }


@app.local_entrypoint()
def main():
    server = modal.Cls.from_name("adaptivecache-server", "LLMServer")()

    print("=" * 65)
    print("LMCache Hole-Leaving Demonstration")
    print("=" * 65)
    print(f"Chunk size: {CHUNK_SIZE} tokens")
    print("Config: LMCACHE_SAVE_DECODE_CACHE=True")
    print("  → LMCache stores ALL KV blocks to CPU after each request")
    print()

    # Build a conversation long enough to fill several LMCache chunks.
    # Each chunk = 256 tokens. We want at least 5 chunks = 1280+ tokens.
    system = (
        "You are a senior software engineer. "
        "Answer precisely in 3-4 sentences. "
        "Stay focused on the technical details. "
    ) * 8  # ~120 tokens

    steps = [
        "Explain how a hash table works internally, including collision handling.",
        "What is the time complexity of hash table operations and why?",
        "How does Python's dict differ from a standard hash table implementation?",
        "Explain consistent hashing and when it's used in distributed systems.",
        "What is a Bloom filter and what problem does it solve?",
    ]

    print("─" * 65)
    print("Step 1: Build conversation (5 steps × ~150 tokens each)")
    print("        → LMCache stores all chunks to CPU (save_decode_cache=True)")
    print("─" * 65)

    messages = [{"role": "system", "content": system}]
    for i, q in enumerate(steps):
        messages.append({"role": "user", "content": q})
        r = server.generate.remote(messages, temperature=0.0, max_tokens=100)
        messages.append({"role": "assistant", "content": r["content"]})
        stats = _parse_lmcache_log(r.get("_lmcache_log", ""))
        print(f"  Step {i+1}: {r['prompt_tokens']:4d} tokens  "
              f"lmc_hit={r.get('lmcache_hit_tokens', 0)}  "
              f"computed={r.get('lmcache_computed_tokens', 0)}")

    final_prompt_tokens = r["prompt_tokens"]
    n_chunks = final_prompt_tokens // CHUNK_SIZE
    print(f"\n  Final prompt: {final_prompt_tokens} tokens = {n_chunks} complete LMCache chunks")

    print()
    print("─" * 65)
    print("Step 2: Replay SAME prompt → should be 100% LMCache CPU hits")
    print("─" * 65)

    # Send all messages except the last assistant turn (to generate fresh)
    prompt_messages = messages[:-1]
    r_warm = server.generate.remote(prompt_messages, temperature=0.0, max_tokens=100)
    warm_hit = r_warm.get("lmcache_hit_tokens", 0)
    warm_computed = r_warm.get("lmcache_computed_tokens", 0)
    print(f"  Warm replay: {r_warm['prompt_tokens']} tokens  "
          f"lmc_hit={warm_hit}  computed={warm_computed}")
    print(f"  → LMCache hit rate: {warm_hit / max(r_warm['prompt_tokens'], 1):.0%}")

    print()
    print("─" * 65)
    mid_chunk = max(1, n_chunks // 2)
    print(f"Step 3: Delete MIDDLE chunk (chunk {mid_chunk}) from LMCache CPU")
    print(f"        Chunk {mid_chunk} covers tokens [{mid_chunk*CHUNK_SIZE}:{(mid_chunk+1)*CHUNK_SIZE}]")
    print("─" * 65)

    token_ids = r_warm["prompt_token_ids"]
    deleted = server.delete_kv_blocks.remote(token_ids, [mid_chunk])
    stats = server.get_stats.remote()
    print(f"  Deleted: {deleted} block(s)")
    print(f"  LMCache stats: {stats}")

    print()
    print("─" * 65)
    print(f"Step 4: Replay SAME prompt (chunk {mid_chunk} deleted)")
    print(f"  Expected: chunks 0..{mid_chunk-1} = LMCache hits  "
          f"| chunk {mid_chunk} = recomputed  "
          f"| chunks {mid_chunk+1}..{n_chunks-1} = LMCache hits")
    print("─" * 65)

    r_hole = server.generate.remote(prompt_messages, temperature=0.0, max_tokens=100)
    hole_hit = r_hole.get("lmcache_hit_tokens", 0)
    hole_computed = r_hole.get("lmcache_computed_tokens", 0)

    print(f"  After deletion: {r_hole['prompt_tokens']} tokens  "
          f"lmc_hit={hole_hit}  computed={hole_computed}")

    expected_computed = CHUNK_SIZE  # Only 1 chunk recomputed
    expected_hit = r_hole["prompt_tokens"] - expected_computed

    print()
    print("=" * 65)
    print("RESULTS")
    print("=" * 65)
    print(f"  Total tokens:         {r_hole['prompt_tokens']}")
    print(f"  Chunks in sequence:   {n_chunks}")
    print(f"  Chunk deleted:        chunk {mid_chunk} (tokens {mid_chunk*256}-{(mid_chunk+1)*256})")
    print()
    print(f"  LMCache hit tokens:   {hole_hit}  "
          f"(expected ≈ {expected_hit}, i.e., all except 1 chunk)")
    print(f"  Tokens recomputed:    {hole_computed}  "
          f"(expected ≈ {expected_computed}, i.e., just chunk {mid_chunk})")
    print()

    if warm_hit > 0 and hole_computed <= CHUNK_SIZE * 2:
        print("✓ HOLE-LEAVING CONFIRMED:")
        print(f"  Deleting 1 middle chunk forced recomputation of only ~{hole_computed} tokens")
        print(f"  Chunks before and after the deleted chunk: still LMCache hits")
        print(f"  Compare: message-level eviction would recompute {r_hole['prompt_tokens'] - mid_chunk*CHUNK_SIZE} tokens")
        print(f"  KV-level eviction savings: "
              f"{(r_hole['prompt_tokens'] - mid_chunk*CHUNK_SIZE - hole_computed)} tokens saved")
    elif warm_hit == 0:
        print("⚠ LMCache CPU hits not yet showing — SAVE_DECODE_CACHE may need")
        print("  a second warm-up or the config env var might not be picked up.")
        print("  Check: redeploy serve.py and run again.")
    else:
        print(f"  Result: {hole_computed} tokens recomputed (1 chunk = {CHUNK_SIZE} expected)")
