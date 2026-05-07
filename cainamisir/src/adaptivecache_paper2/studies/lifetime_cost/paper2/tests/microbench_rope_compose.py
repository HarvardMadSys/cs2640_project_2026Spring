"""Verify RoPE composition: R(p+Δ) @ x  ==  R(Δ) @ R(p) @ x.

If this passes, the Phase 9 plan to re-position cached K vectors via a
post-hoc rotation by Δ tokens is mathematically sound. If it fails, the
plan is dead and we need a different strategy.

Run:
  /home/vlad/adaptivecache/.venv/bin/python -m studies.lifetime_cost.paper2.tests.microbench_rope_compose
"""
from __future__ import annotations

import torch


# Match Qwen3-30B-A3B params (head_size=128, base=10000, is_neox_style)
HEAD_SIZE = 128
ROTARY_DIM = 128  # full head, no partial RoPE
BASE = 10000.0


def make_cos_sin_cache(max_pos: int, dim: int, base: float) -> torch.Tensor:
    """Same shape as vLLM's cos_sin_cache: (max_pos, dim) with first half cos, second half sin."""
    inv_freq = 1.0 / (base ** (torch.arange(0, dim, 2, dtype=torch.float32) / dim))
    t = torch.arange(max_pos, dtype=torch.float32)
    freqs = torch.outer(t, inv_freq)  # (max_pos, dim/2)
    cos = freqs.cos()
    sin = freqs.sin()
    return torch.cat([cos, sin], dim=-1)  # (max_pos, dim)


def apply_rope(
    x: torch.Tensor,           # (n_tokens, n_heads, head_size)
    positions: torch.Tensor,   # (n_tokens,) int64
    cos_sin: torch.Tensor,     # (max_pos, dim) float32
    is_neox: bool = True,
) -> torch.Tensor:
    """Apply RoPE at given positions. NeoX style: rotate first half against second half."""
    pos = positions.long()
    cs = cos_sin[pos]                      # (n_tokens, dim)
    half = cs.shape[-1] // 2
    cos = cs[..., :half].unsqueeze(1)      # (n_tokens, 1, half)
    sin = cs[..., half:].unsqueeze(1)
    # NeoX style: split last dim in half (not interleaved)
    x1 = x[..., :half]
    x2 = x[..., half:]
    out1 = x1 * cos - x2 * sin
    out2 = x2 * cos + x1 * sin
    return torch.cat([out1, out2], dim=-1)


def main() -> None:
    torch.manual_seed(0)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float32  # use fp32 for the equality check; we'll re-test in bf16 separately

    max_pos = 8192
    n_tokens = 256
    n_heads = 8
    cos_sin = make_cos_sin_cache(max_pos, ROTARY_DIM, BASE).to(device=device, dtype=dtype)

    # Random un-rotated K
    k_unrot = torch.randn(n_tokens, n_heads, HEAD_SIZE, device=device, dtype=dtype)

    # Compose test: pick p, delta, target_pos = p + delta
    cases = [
        (0, 0),
        (16, 16),
        (100, 0),
        (0, 100),
        (1024, 2048),     # large delta
        (3000, 4000),     # near max
        (16, 80),         # delta % block_size = 0 (block_size=16)
        (16, 81),         # delta % block_size != 0
    ]

    print(f"{'p':>6} {'delta':>6} {'target=p+delta':>14}   {'l2_err':>10}   {'rel_err':>10}   {'cos_sim':>10}   pass?")
    all_pass = True
    for p, delta in cases:
        target = p + delta
        if target >= max_pos:
            print(f"  skip p={p} delta={delta}: out of cache range")
            continue
        positions_p = torch.full((n_tokens,), p, device=device, dtype=torch.long)
        positions_d = torch.full((n_tokens,), delta, device=device, dtype=torch.long)
        positions_t = torch.full((n_tokens,), target, device=device, dtype=torch.long)

        # path A: rotate by p, then rotate by delta
        k_p = apply_rope(k_unrot, positions_p, cos_sin)
        k_pd = apply_rope(k_p, positions_d, cos_sin)

        # path B: rotate by p+delta in one shot
        k_t = apply_rope(k_unrot, positions_t, cos_sin)

        err = (k_pd - k_t)
        # Vector-level relative error: ||k_pd - k_t|| / ||k_t||. RoPE preserves
        # magnitude, so this is the right metric. Per-element relative error
        # is misleading because individual entries can be near-zero by chance.
        l2_err = err.flatten().norm().item()
        l2_t = k_t.flatten().norm().item()
        rel = l2_err / max(l2_t, 1e-8)
        cos_sim = torch.nn.functional.cosine_similarity(
            k_pd.flatten().unsqueeze(0), k_t.flatten().unsqueeze(0)
        ).item()
        ok = rel < 1e-3
        all_pass = all_pass and ok
        print(f"{p:>6} {delta:>6} {target:>14}   {l2_err:>10.3e}   {rel:>10.3e}   {cos_sim:>10.7f}   {'ok' if ok else 'FAIL'}")

    print()
    if all_pass:
        print("RoPE composition holds: R(p+Δ) == R(Δ) ∘ R(p). Phase 9 is mathematically sound.")
    else:
        raise AssertionError("RoPE composition failed — Phase 9 plan is broken.")

    # Sanity: also test bf16 (vLLM's actual dtype)
    print()
    print("Sanity check in bf16 (vLLM's actual cache dtype):")
    cos_sin_bf = cos_sin.to(torch.bfloat16)
    k_unrot_bf = k_unrot.to(torch.bfloat16)
    p, delta = 1024, 2048
    pos_p = torch.full((n_tokens,), p, device=device, dtype=torch.long)
    pos_d = torch.full((n_tokens,), delta, device=device, dtype=torch.long)
    pos_t = torch.full((n_tokens,), p + delta, device=device, dtype=torch.long)
    k_p = apply_rope(k_unrot_bf, pos_p, cos_sin_bf)
    k_pd = apply_rope(k_p, pos_d, cos_sin_bf)
    k_t = apply_rope(k_unrot_bf, pos_t, cos_sin_bf)
    err = (k_pd.float() - k_t.float()).abs()
    print(f"  bf16 max_abs_err = {err.max().item():.3e}, mean = {err.mean().item():.3e}")
    print(f"  (bf16 compose vs single-shot. Difference is from bf16 rounding in two ops vs one.)")


if __name__ == "__main__":
    main()
