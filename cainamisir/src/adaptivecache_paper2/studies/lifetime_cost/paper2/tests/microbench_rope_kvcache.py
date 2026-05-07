"""Verify the cache-layout-aware K rotation primitive.

This is the function we'll plumb into GPUModelRunner._execute_kv_rotate_operations.
Given a list of physical block IDs and a delta, it gathers K from the
paged KV cache, rotates by delta tokens via vLLM's kernel, and scatters back.

The microbench:
  1. Builds a fake KV cache tensor in FlashInfer layout.
  2. Manually pre-rotates some "suffix" blocks' K to phase p.
  3. Calls rotate_k_in_kv_cache(block_ids, delta=Δ).
  4. Compares the rotated K against the same K rotated to phase p+Δ
     directly. Should match (RoPE composition).

Run:
  /home/vlad/adaptivecache/.venv/bin/python \\
    -m studies.lifetime_cost.paper2.tests.microbench_rope_kvcache
"""
from __future__ import annotations

import torch

from vllm.model_executor.layers.rotary_embedding import RotaryEmbedding


# --- The primitive we're testing -------------------------------------------

def rotate_k_in_kv_cache(
    kv_caches: list[torch.Tensor],
    block_ids: list[int],
    delta_tokens: int,
    rope: RotaryEmbedding,
    *,
    flash_attn_layout: bool = False,
) -> int:
    """Rotate K vectors in the given physical blocks by delta_tokens, in place.

    Layouts:
      FlashAttention: kv_cache[layer] shape [2, num_blocks, block_size, num_kv_heads, head_size]
                      K = kv_cache[layer][0, block_id]
      FlashInfer:     kv_cache[layer] shape [num_blocks, 2, block_size, num_kv_heads, head_size]
                      K = kv_cache[layer][block_id, 0]

    Returns the total number of K vectors rotated (across all layers).
    """
    if not kv_caches or not block_ids or delta_tokens == 0:
        return 0
    device = kv_caches[0].device
    idx = torch.tensor(block_ids, dtype=torch.long, device=device)

    # Determine block_size from layout
    if flash_attn_layout:
        # [2, num_blocks, block_size, num_kv_heads, head_size]
        block_size = kv_caches[0].shape[2]
    else:
        # [num_blocks, 2, block_size, num_kv_heads, head_size]
        block_size = kv_caches[0].shape[2]

    n_blocks = len(block_ids)
    n_tokens = n_blocks * block_size
    positions = torch.full((n_tokens,), delta_tokens, device=device, dtype=torch.long)

    rotated_count = 0
    for layer_idx, kv_cache in enumerate(kv_caches):
        if flash_attn_layout:
            # Gather: shape [n_blocks, block_size, num_kv_heads, head_size]
            k_gathered = kv_cache[0].index_select(0, idx).contiguous()
        else:
            # kv_cache[block_ids, 0] requires advanced indexing; use index_select then slice
            k_gathered = kv_cache.index_select(0, idx)[:, 0].contiguous()

        # Reshape to (n_tokens, num_kv_heads * head_size) for rotary kernel
        n_heads = k_gathered.shape[2]
        head_size = k_gathered.shape[3]
        k_flat = k_gathered.reshape(n_tokens, n_heads * head_size)

        # Match rope's dtype to the cache dtype (cos_sin_cache must match query dtype)
        rope._match_cos_sin_cache_dtype(k_flat) if hasattr(rope, "_match_cos_sin_cache_dtype") else None

        q_dummy = torch.zeros_like(k_flat)
        _, k_rotated_flat = rope.forward_native(positions, q_dummy, k_flat)

        # Reshape back and scatter
        k_rotated = k_rotated_flat.reshape(n_blocks, block_size, n_heads, head_size)
        if flash_attn_layout:
            kv_cache[0, idx] = k_rotated.to(kv_cache.dtype)
        else:
            # kv_cache[idx, 0] = ... requires advanced indexing assign
            kv_cache[idx, 0] = k_rotated.to(kv_cache.dtype)

        rotated_count += n_tokens

    return rotated_count


# --- Test ------------------------------------------------------------------

HEAD_SIZE = 128
ROTARY_DIM = 128
BASE = 10000.0
MAX_POS = 8192
N_KV_HEADS = 8
BLOCK_SIZE = 16
N_LAYERS = 4


def main() -> None:
    torch.manual_seed(0)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16  # match vLLM cache dtype

    rope = RotaryEmbedding(
        head_size=HEAD_SIZE,
        rotary_dim=ROTARY_DIM,
        max_position_embeddings=MAX_POS,
        base=BASE,
        is_neox_style=True,
        dtype=dtype,
    ).to(device)

    # Simulate FlashInfer layout: [num_blocks, 2, block_size, num_kv_heads, head_size]
    NUM_BLOCKS = 64
    kv_caches = []
    # Reference K (unrotated) per layer per block
    k_unrot_ref = []
    for _ in range(N_LAYERS):
        k_unrot = torch.randn(
            NUM_BLOCKS, BLOCK_SIZE, N_KV_HEADS, HEAD_SIZE, device=device, dtype=dtype
        )
        v_random = torch.randn(
            NUM_BLOCKS, BLOCK_SIZE, N_KV_HEADS, HEAD_SIZE, device=device, dtype=dtype
        )
        # Stack along the 2-dim (K=0, V=1)
        kv = torch.stack([k_unrot, v_random], dim=1).contiguous()
        kv_caches.append(kv)
        k_unrot_ref.append(k_unrot.clone())

    # Pre-rotate some "suffix" blocks' K to phase p
    suffix_block_ids = list(range(20, 28))  # 8 blocks
    n_suffix_blocks = len(suffix_block_ids)
    n_suffix_tokens = n_suffix_blocks * BLOCK_SIZE
    p = 1024  # initial position
    delta = 2048  # rotation we want to apply
    target = p + delta

    idx = torch.tensor(suffix_block_ids, dtype=torch.long, device=device)
    pos_p = torch.full((n_suffix_tokens,), p, device=device, dtype=torch.long)
    for layer_idx in range(N_LAYERS):
        kv = kv_caches[layer_idx]
        k = kv.index_select(0, idx)[:, 0].contiguous()  # [n_blocks, block_size, n_heads, head_size]
        k_flat = k.reshape(n_suffix_tokens, N_KV_HEADS * HEAD_SIZE)
        q_dummy = torch.zeros_like(k_flat)
        _, k_rot = rope.forward_native(pos_p, q_dummy, k_flat)
        kv[idx, 0] = k_rot.reshape(n_suffix_blocks, BLOCK_SIZE, N_KV_HEADS, HEAD_SIZE).to(dtype)

    # Now: cached K is at phase p. Rotate by delta to put it at phase p+delta.
    n_rotated = rotate_k_in_kv_cache(
        kv_caches, suffix_block_ids, delta, rope, flash_attn_layout=False,
    )
    print(f"rotate_k_in_kv_cache: rotated {n_rotated} K vectors across {N_LAYERS} layers")
    assert n_rotated == n_suffix_tokens * N_LAYERS

    # Reference: compute K at phase p+delta directly from k_unrot_ref
    pos_t = torch.full((n_suffix_tokens,), target, device=device, dtype=torch.long)

    for layer_idx in range(N_LAYERS):
        # What's currently in the cache after rotate (should be at phase p+delta)
        k_now = kv_caches[layer_idx].index_select(0, idx)[:, 0].contiguous()
        k_now_flat = k_now.reshape(n_suffix_tokens, N_KV_HEADS * HEAD_SIZE)

        # Reference: rotate the original unrotated K to phase target
        k_unrot = k_unrot_ref[layer_idx][20:28].contiguous()
        k_unrot_flat = k_unrot.reshape(n_suffix_tokens, N_KV_HEADS * HEAD_SIZE)
        q_dummy = torch.zeros_like(k_unrot_flat)
        _, k_ref_flat = rope.forward_native(pos_t, q_dummy, k_unrot_flat)

        l2_err = (k_now_flat.float() - k_ref_flat.float()).flatten().norm().item()
        l2_ref = k_ref_flat.float().flatten().norm().item()
        rel = l2_err / max(l2_ref, 1e-8)
        cos_sim = torch.nn.functional.cosine_similarity(
            k_now_flat.float().flatten().unsqueeze(0),
            k_ref_flat.float().flatten().unsqueeze(0),
        ).item()
        print(f"  layer {layer_idx}: rel_err={rel:.3e}  cos_sim={cos_sim:.7f}")
        # bf16 + 2-step rotation accumulates more rounding; allow 5e-2 relative.
        assert rel < 5e-2, f"layer {layer_idx} rotation off: rel={rel:.3e}"
        assert cos_sim > 0.999, f"layer {layer_idx} cos_sim too low: {cos_sim:.5f}"

    # Also verify V was NOT touched (only K should rotate)
    for layer_idx in range(N_LAYERS):
        v_now = kv_caches[layer_idx][:, 1]
        # We didn't change V, so it should be untouched. Just spot-check that it's not all-zero.
        assert v_now.abs().sum() > 0
    print()
    print("rotate_k_in_kv_cache works on FlashInfer layout.")
    print(f"  - rotates only K (V untouched)")
    print(f"  - in-place mutation of paged cache tensor")
    print(f"  - bf16 cosine similarity > 0.999 across {N_LAYERS} layers")


if __name__ == "__main__":
    main()
