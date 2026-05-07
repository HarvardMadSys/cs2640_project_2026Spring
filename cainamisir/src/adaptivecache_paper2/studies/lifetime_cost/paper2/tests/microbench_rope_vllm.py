"""Verify vLLM's RotaryEmbedding kernel composes correctly: applying
forward_static twice (R(p) then R(Δ)) equals applying it once at p+Δ.

If this holds, we can re-use vLLM's optimized kernel for the Phase 9
in-cache K rotation — no need to write a new CUDA kernel.

Run:
  /home/vlad/adaptivecache/.venv/bin/python \\
    -m studies.lifetime_cost.paper2.tests.microbench_rope_vllm
"""
from __future__ import annotations

import torch

from vllm.model_executor.layers.rotary_embedding.base import RotaryEmbedding


# Match Qwen3-30B-A3B-ish params; the exact base is irrelevant for the
# composition property — what matters is consistency between the two
# rotation paths.
HEAD_SIZE = 128
ROTARY_DIM = 128
BASE = 10000.0
MAX_POS = 8192
N_HEADS = 8
N_TOKENS = 256
IS_NEOX = True
DTYPE = torch.float32  # we'll also test bf16 below


def main() -> None:
    torch.manual_seed(0)
    device = "cuda" if torch.cuda.is_available() else "cpu"

    rope = RotaryEmbedding(
        head_size=HEAD_SIZE,
        rotary_dim=ROTARY_DIM,
        max_position_embeddings=MAX_POS,
        base=BASE,
        is_neox_style=IS_NEOX,
        dtype=DTYPE,
    )
    rope.to(device)

    cos_sin = rope.cos_sin_cache  # (max_pos, rotary_dim)
    # Random un-rotated K and a dummy Q (forward_static needs both).
    k_unrot = torch.randn(N_TOKENS, N_HEADS, HEAD_SIZE, device=device, dtype=DTYPE)
    k_flat_shape = (N_TOKENS, N_HEADS * HEAD_SIZE)

    cases = [
        (0, 0),
        (16, 16),
        (1024, 2048),
        (3000, 4000),
        (16, 80),     # delta block-aligned (16 = block_size)
        (16, 81),     # delta NOT block-aligned (worst case for our use)
    ]

    print(f"{'p':>6} {'delta':>6} {'p+delta':>8}   {'rel_err':>10}   {'cos_sim':>10}   pass?")
    all_pass = True
    for p, delta in cases:
        target = p + delta
        if target >= MAX_POS:
            continue

        # Path A: apply rotation by p via forward_static, then rotation by delta
        positions_p = torch.full((N_TOKENS,), p, device=device, dtype=torch.long)
        positions_d = torch.full((N_TOKENS,), delta, device=device, dtype=torch.long)
        k1 = k_unrot.clone().view(*k_flat_shape)
        q_dummy = torch.zeros_like(k1)
        _, k1 = rope.forward_native(positions_p, q_dummy, k1)
        _, k1 = rope.forward_native(positions_d, q_dummy, k1)

        # Path B: apply rotation by p+delta directly
        positions_t = torch.full((N_TOKENS,), target, device=device, dtype=torch.long)
        k2 = k_unrot.clone().view(*k_flat_shape)
        _, k2 = rope.forward_native(positions_t, q_dummy, k2)

        l2_err = (k1 - k2).flatten().norm().item()
        l2_t = k2.flatten().norm().item()
        rel = l2_err / max(l2_t, 1e-8)
        cos_sim = torch.nn.functional.cosine_similarity(
            k1.flatten().unsqueeze(0), k2.flatten().unsqueeze(0)
        ).item()
        ok = rel < 1e-3
        all_pass = all_pass and ok
        print(f"{p:>6} {delta:>6} {target:>8}   {rel:>10.3e}   {cos_sim:>10.7f}   {'ok' if ok else 'FAIL'}")

    print()
    if all_pass:
        print("vLLM RotaryEmbedding.forward_static composes correctly.")
        print("→ Phase 9 can re-use the existing kernel; no custom CUDA needed.")
    else:
        raise AssertionError("vLLM RoPE kernel does NOT compose — need to investigate.")

    # bf16 sanity (vLLM's actual KV cache dtype on H100/Blackwell)
    print()
    print("bf16 sanity:")
    rope_bf = RotaryEmbedding(HEAD_SIZE, ROTARY_DIM, MAX_POS, BASE, IS_NEOX, torch.bfloat16).to(device)
    cos_sin_bf = rope_bf.cos_sin_cache
    k_unrot_bf = k_unrot.to(torch.bfloat16)
    p, delta = 1024, 2048
    pos_p = torch.full((N_TOKENS,), p, device=device, dtype=torch.long)
    pos_d = torch.full((N_TOKENS,), delta, device=device, dtype=torch.long)
    pos_t = torch.full((N_TOKENS,), p + delta, device=device, dtype=torch.long)
    k1 = k_unrot_bf.clone().view(*k_flat_shape)
    q_dummy = torch.zeros_like(k1)
    _, k1 = rope_bf.forward_native(pos_p, q_dummy, k1)
    _, k1 = rope_bf.forward_native(pos_d, q_dummy, k1)
    k2 = k_unrot_bf.clone().view(*k_flat_shape)
    _, k2 = rope_bf.forward_native(pos_t, q_dummy, k2)
    err = (k1.float() - k2.float()).abs()
    print(f"  bf16 max_abs={err.max().item():.3e}  mean_abs={err.mean().item():.3e}")


if __name__ == "__main__":
    main()
