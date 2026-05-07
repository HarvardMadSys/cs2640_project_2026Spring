"""Byte- and token-level common prefix between two message sequences.

This is what determines the *upper bound* on what a perfect prefix cache
could have hit at step t given step t-1. The cliff metric is computed
directly from this.

The serialization here must mirror what providers actually hash for prefix
caching — they hash the rendered prompt string (chat template applied),
not the JSON message list. We approximate with a stable canonical form
that is provider-portable; for an exact comparison against a specific
provider, plug their chat template in via `render` parameter.
"""

from __future__ import annotations

import hashlib
import json
from typing import Callable, Iterable, List, Sequence


def canonical_render(messages: Iterable[dict]) -> str:
    """Provider-neutral canonical rendering. Stable across runs."""
    out = []
    for m in messages:
        out.append(f"<|{m.get('role', '?')}|>")
        if isinstance(m.get("content"), str):
            out.append(m["content"])
        elif m.get("content") is None and m.get("tool_calls"):
            out.append(json.dumps(m["tool_calls"], sort_keys=True))
        if m.get("name"):
            out.append(f"<|name:{m['name']}|>")
        if m.get("tool_call_id"):
            out.append(f"<|tcid:{m['tool_call_id']}|>")
        out.append("<|end|>\n")
    return "".join(out)


def common_prefix_chars(a: str, b: str) -> int:
    """Length of longest common prefix in characters."""
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def common_prefix_tokens(a: List[int], b: List[int]) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def hash_prefix_blocks(text: str, block_chars: int = 1024) -> List[str]:
    """Block-level hash chain. Approximates how vLLM/LMCache index prefix
    cache entries (hash chain over fixed-size blocks)."""
    hashes = []
    prev = b""
    for i in range(0, len(text), block_chars):
        h = hashlib.sha256(prev + text[i : i + block_chars].encode("utf-8")).digest()
        hashes.append(h.hex()[:16])
        prev = h
    return hashes


def common_prefix_blocks(a_hashes: List[str], b_hashes: List[str]) -> int:
    """Number of leading blocks shared between two hash chains."""
    n = min(len(a_hashes), len(b_hashes))
    i = 0
    while i < n and a_hashes[i] == b_hashes[i]:
        i += 1
    return i


def cache_hit_ceiling(
    msgs_prev: Sequence[dict],
    msgs_curr: Sequence[dict],
    *,
    render: Callable[[Iterable[dict]], str] = canonical_render,
    block_chars: int = 1024,
) -> dict:
    """Compute the upper bound on prefix-cache hit at step t given step t-1.

    Returns a dict with:
      curr_chars, curr_blocks: total length of the current prompt
      shared_chars, shared_blocks: leading shared portion
      hit_ratio_chars: shared / curr_chars
      hit_ratio_blocks: shared / curr_blocks
    """
    a = render(msgs_prev)
    b = render(msgs_curr)
    a_hashes = hash_prefix_blocks(a, block_chars)
    b_hashes = hash_prefix_blocks(b, block_chars)

    sc = common_prefix_chars(a, b)
    sb = common_prefix_blocks(a_hashes, b_hashes)

    return {
        "curr_chars": len(b),
        "curr_blocks": len(b_hashes),
        "shared_chars": sc,
        "shared_blocks": sb,
        "hit_ratio_chars": sc / max(len(b), 1),
        "hit_ratio_blocks": sb / max(len(b_hashes), 1),
    }
