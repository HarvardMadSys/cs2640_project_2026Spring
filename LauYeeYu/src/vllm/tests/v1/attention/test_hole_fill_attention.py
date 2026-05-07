# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
"""
Tests for one-step hole-fill attention correctness.

Verifies that the cascade attention decomposition (shared prefix + per-gap
suffix + merge_state) produces numerically correct results compared to
standard causal attention computed via flex_attention.
"""

import unittest.mock
from functools import partial

import pytest
import torch
from torch.nn.attention.flex_attention import create_block_mask, flex_attention

from tests.v1.attention.utils import (
    BatchSpec,
    create_common_attn_metadata,
    create_standard_kv_cache_spec,
    create_vllm_config,
    try_backend_includes_kv_cache_update,
    try_get_attention_backend,
)
from vllm.config import set_current_vllm_config
from vllm.platforms import current_platform
from vllm.utils.math_utils import cdiv
from vllm.utils.torch_utils import set_random_seed
from vllm.v1.attention.backends.registry import AttentionBackendEnum
from vllm.v1.attention.backends.utils import set_kv_cache_layout

try:
    import flashinfer  # noqa: F401
    HAS_FLASHINFER = True
except ImportError:
    HAS_FLASHINFER = False


def causal_mask(b, h, q_idx, kv_idx, context_len=0):
    """Standard causal mask: token q attends to kv if kv <= context_len + q."""
    return kv_idx <= context_len + q_idx


class MockAttentionLayer:
    def __init__(self, device: torch.device):
        self._q_scale = torch.tensor(1.0, device=device)
        self._k_scale = torch.tensor(1.0, device=device)
        self._v_scale = torch.tensor(1.0, device=device)
        self._q_scale_float = 1.0
        self._k_scale_float = 1.0
        self._v_scale_float = 1.0


def _run_hole_fill_test(
    seq_len: int,
    gap_ranges: list[tuple[int, int]],
    block_size: int = 16,
    atol: float = 1e-2,
    rtol: float = 1e-2,
):
    """
    Test hole-fill attention correctness for a single request.

    Sets up a sequence of length `seq_len` with cached segments and gaps
    defined by `gap_ranges`. Gap tokens are the "new" tokens scheduled by
    the hole-fill path.

    For example, with seq_len=80 and gap_ranges=[(16,16), (48,16)]:
      - Cached segment 1: tokens 0-15 (prefix)
      - Gap 1: tokens 16-31
      - Cached segment 2: tokens 32-47
      - Gap 2: tokens 48-63
      - Cached segment 3: tokens 64-79

    The test computes:
      1. Reference: full causal attention for gap tokens using flex_attention
      2. Hole-fill: FlashInfer cascade attention (prefix + suffix + merge)
    And verifies they match within tolerance.
    """
    set_random_seed(42)
    device = torch.device("cuda:0")

    model_name = "facebook/opt-125m"
    vllm_config = create_vllm_config(
        model_name=model_name,
        max_model_len=max(seq_len, 1024),
        block_size=block_size,
        num_gpu_blocks=8192,
    )

    num_q_heads = vllm_config.model_config.get_num_attention_heads(
        vllm_config.parallel_config)
    num_kv_heads = vllm_config.model_config.get_num_kv_heads(
        vllm_config.parallel_config)
    head_size = vllm_config.model_config.get_head_size()
    dtype = torch.float16
    scale = 1.0 / (head_size ** 0.5)

    # Total gap tokens = the "query" tokens for the hole-fill request.
    total_gap_tokens = sum(gl for _, gl in gap_ranges)

    # Generate Q, K, V for the FULL sequence.
    k_full = torch.randn(
        seq_len, num_kv_heads, head_size, dtype=dtype, device=device)
    v_full = torch.randn(
        seq_len, num_kv_heads, head_size, dtype=dtype, device=device)

    # Q is only for gap tokens.
    gap_q_parts = []
    for gap_start, gap_len in gap_ranges:
        gap_q_parts.append(
            torch.randn(
                gap_len, num_q_heads, head_size, dtype=dtype, device=device))

    query = torch.cat(gap_q_parts, dim=0)
    assert query.shape[0] == total_gap_tokens

    # --- Reference: flex_attention for each gap ---
    # For each gap token at global position p, it attends causally to
    # positions 0..p in the full K/V sequence.
    all_ref_outputs = []
    for gap_idx, (gap_start, gap_len) in enumerate(gap_ranges):
        q_gap = gap_q_parts[gap_idx]
        # KV context for this gap: positions 0..gap_start+gap_len-1
        kv_len = gap_start + gap_len
        k_ctx = k_full[:kv_len]
        v_ctx = v_full[:kv_len]

        # Expand for GQA if needed.
        q_sdpa = q_gap.unsqueeze(0).transpose(1, 2)  # [1,H,Q,D]
        k_sdpa = k_ctx.unsqueeze(0).transpose(1, 2)  # [1,Hkv,KV,D]
        v_sdpa = v_ctx.unsqueeze(0).transpose(1, 2)

        if num_q_heads != num_kv_heads:
            repeats = num_q_heads // num_kv_heads
            k_sdpa = k_sdpa.repeat_interleave(repeats, dim=1)
            v_sdpa = v_sdpa.repeat_interleave(repeats, dim=1)

        # Causal mask: gap token i (at global position gap_start+i) attends
        # to kv positions 0..gap_start+i.
        # In flex_attention terms, with context_len=gap_start:
        #   token q_idx attends to kv_idx if kv_idx <= gap_start + q_idx
        mask_fn = partial(causal_mask, context_len=gap_start)
        block_mask = create_block_mask(
            mask_fn, B=None, H=None, Q_LEN=gap_len, KV_LEN=kv_len,
            device=device)
        ref_out = flex_attention(
            q_sdpa, k_sdpa, v_sdpa,
            block_mask=block_mask, scale=scale, enable_gqa=True)
        all_ref_outputs.append(ref_out.transpose(1, 2).squeeze(0))

    ref_output = torch.cat(all_ref_outputs, dim=0)

    # --- Set up FlashInfer hole-fill path ---
    # Treat the entire request as a single hole-fill prefill.
    # seq_lens = [seq_len], query_lens = [total_gap_tokens]
    batch_spec = BatchSpec(
        seq_lens=[seq_len],
        query_lens=[total_gap_tokens],
        name="hole_fill_test",
    )

    common_attn_metadata = create_common_attn_metadata(
        batch_spec, block_size, device)

    # Populate KV cache with the FULL sequence K/V.
    num_blocks_needed = cdiv(seq_len, block_size)
    num_blocks_total = vllm_config.cache_config.num_gpu_blocks or 8192

    # Create KV cache: [2, num_blocks, block_size, num_kv_heads, head_size]
    kv_cache = torch.zeros(
        2, num_blocks_total, block_size, num_kv_heads, head_size,
        dtype=dtype, device=device)

    # Place the full K/V into sequential blocks starting at block 1.
    kv_cache_flat = kv_cache.view(2, -1, num_kv_heads, head_size)
    start = 1 * block_size  # Skip block 0 (null block)
    kv_cache_flat[0, start:start + seq_len] = k_full
    kv_cache_flat[1, start:start + seq_len] = v_full

    # Set up block table: sequential blocks 1, 2, 3, ...
    block_table = common_attn_metadata.block_table_tensor
    for b in range(num_blocks_needed):
        block_table[0, b] = b + 1

    # Set up slot mapping for gap tokens (where new K/V would be written).
    slot_mapping = common_attn_metadata.slot_mapping
    offset = 0
    for gap_start, gap_len in gap_ranges:
        for t in range(gap_len):
            global_pos = gap_start + t
            block_idx = global_pos // block_size
            block_offset = global_pos % block_size
            physical_block = int(block_table[0, block_idx].item())
            slot_mapping[offset + t] = (
                physical_block * block_size + block_offset)
        offset += gap_len

    # Set hole_fill_gap_ranges on the metadata (keyed by batch index).
    common_attn_metadata.hole_fill_gap_ranges = {0: gap_ranges}

    # Convert to FlashInfer layout: [num_blocks, 2, block_size, H, D]
    kv_cache_fi = kv_cache.transpose(0, 1)
    # FlashInfer HND layout
    kv_cache_fi = kv_cache_fi.transpose(2, 3).contiguous().transpose(2, 3)
    set_kv_cache_layout("HND")

    try:
        kv_cache_spec = create_standard_kv_cache_spec(vllm_config)
        builder_cls, impl_cls = try_get_attention_backend(
            AttentionBackendEnum.FLASHINFER)

        from vllm.v1.attention.backends.utils import PerLayerParameters

        def mock_get_per_layer_parameters(vc, layer_names, impl_cls):
            hs = vc.model_config.get_head_size()
            return {
                name: PerLayerParameters(
                    window_left=-1,
                    logits_soft_cap=0.0,
                    sm_scale=1.0 / (hs ** 0.5),
                )
                for name in layer_names
            }

        with set_current_vllm_config(vllm_config), \
             unittest.mock.patch(
                 "vllm.v1.attention.backends.flashinfer"
                 ".get_per_layer_parameters",
                 mock_get_per_layer_parameters,
             ):
            builder = builder_cls(
                kv_cache_spec, ["placeholder"], vllm_config, device)
            attn_metadata = builder.build(
                common_prefix_len=0,
                common_attn_metadata=common_attn_metadata,
            )

            # Verify hole-fill was detected.
            assert attn_metadata.use_hole_fill, (
                "Expected use_hole_fill=True but got False")

            # Build K/V for the gap tokens (used for reshape_and_cache).
            gap_k_parts = []
            gap_v_parts = []
            for gap_start, gap_len in gap_ranges:
                gap_k_parts.append(k_full[gap_start:gap_start + gap_len])
                gap_v_parts.append(v_full[gap_start:gap_start + gap_len])
            key = torch.cat(gap_k_parts, dim=0)
            value = torch.cat(gap_v_parts, dim=0)

            mock_layer = MockAttentionLayer(device)
            output = torch.empty_like(query)

            impl = impl_cls(
                num_heads=num_q_heads,
                head_size=head_size,
                scale=scale,
                num_kv_heads=num_kv_heads,
                alibi_slopes=None,
                sliding_window=None,
                kv_cache_dtype="auto",
            )

            if not try_backend_includes_kv_cache_update(
                    AttentionBackendEnum.FLASHINFER):
                impl.do_kv_cache_update(
                    mock_layer, key, value, kv_cache_fi,
                    attn_metadata.slot_mapping)

            output = impl.forward(
                mock_layer, query, key, value, kv_cache_fi, attn_metadata,
                output=output)

    finally:
        set_kv_cache_layout(None)

    # Compare hole-fill output to reference.
    assert output.shape == ref_output.shape, (
        f"Shape mismatch: {output.shape} vs {ref_output.shape}")
    assert torch.isfinite(output).all(), "Hole-fill output has non-finite values"

    torch.testing.assert_close(
        output, ref_output, rtol=rtol, atol=atol,
        msg=lambda msg: (
            f"Hole-fill attention output differs from reference. {msg}"))


@pytest.mark.skipif(not HAS_FLASHINFER, reason="FlashInfer not available")
@pytest.mark.skipif(not current_platform.is_cuda(), reason="CUDA required")
class TestHoleFillAttention:
    """Test suite for one-step hole-fill attention correctness."""

    def test_two_gaps_block_aligned(self):
        """Two gaps with block-aligned boundaries.
        Layout: [seg0: 0-15] [gap0: 16-31] [seg1: 32-47] [gap1: 48-63]
        """
        _run_hole_fill_test(
            seq_len=64,
            gap_ranges=[(16, 16), (48, 16)],
            block_size=16,
        )

    def test_three_gaps(self):
        """Three gaps with varying sizes.
        Layout: [seg0: 0-15] [gap0: 16-31] [seg1: 32-47] [gap1: 48-63]
                [seg2: 64-79] [gap2: 80-95]
        """
        _run_hole_fill_test(
            seq_len=96,
            gap_ranges=[(16, 16), (48, 16), (80, 16)],
            block_size=16,
        )

    def test_large_prefix_small_gaps(self):
        """Large shared prefix with two small gaps.
        Layout: [seg0: 0-63] [gap0: 64-79] [seg1: 80-95] [gap1: 96-111]
        """
        _run_hole_fill_test(
            seq_len=112,
            gap_ranges=[(64, 16), (96, 16)],
            block_size=16,
        )

    def test_uneven_gap_sizes(self):
        """Gaps of different sizes (but still block-aligned).
        Layout: [seg0: 0-15] [gap0: 16-47] [seg1: 48-63] [gap1: 64-79]
        """
        _run_hole_fill_test(
            seq_len=80,
            gap_ranges=[(16, 32), (64, 16)],
            block_size=16,
        )

    def test_no_prefix_gap_at_start(self):
        """Gap starts at position 0 (no shared prefix).
        Layout: [gap0: 0-15] [seg0: 16-31] [gap1: 32-47]
        """
        _run_hole_fill_test(
            seq_len=48,
            gap_ranges=[(0, 16), (32, 16)],
            block_size=16,
        )

    def test_small_block_size(self):
        """Smaller block size with multiple gaps."""
        _run_hole_fill_test(
            seq_len=64,
            gap_ranges=[(8, 8), (24, 8)],
            block_size=8,
        )

    def test_single_gap_with_prefix_and_suffix(self):
        """Single cached prefix, one gap, one cached suffix, trailing gap.
        Even though one gap wouldn't normally trigger hole-fill (needs >1),
        this tests the cascade attention math with two gaps.
        Layout: [seg0: 0-31] [gap0: 32-47] [seg1: 48-79] [gap1: 80-95]
        """
        _run_hole_fill_test(
            seq_len=96,
            gap_ranges=[(32, 16), (80, 16)],
            block_size=16,
        )

    def test_many_small_gaps(self):
        """Four small gaps to stress the cascade merge."""
        _run_hole_fill_test(
            seq_len=128,
            gap_ranges=[(16, 16), (48, 16), (80, 16), (112, 16)],
            block_size=16,
        )


# --------------------------------------------------------------------------
# Scheduler regression tests for _allocate_slots_for_holes.
#
# The attention-correctness tests above build FlashInferMetadata directly
# and bypass the scheduler. The following tests exercise the scheduler
# path itself, because the bug that motivates them lives in
# `Scheduler._allocate_slots_for_holes`: for iterations i >= 1 it used to
# pick `segments[seg_idx]` as the "segment preceding this gap", but by
# construction `segments[seg_idx]` is the segment *following* the current
# gap (the previous iteration's swallow already consumed the preceding
# one). Linking it as preceding placed the segment at the wrong offset in
# req_to_blocks and could leave gap blocks unhashed — which later blew up
# `record_request_blocks` with "Full blocks detected after unhashed
# blocks" on any radix-tree eviction policy.
# --------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402


def _make_hole_fill_scheduler(block_size: int = 10):
    """Build a scheduler with a mocked KVCacheManager that records every
    allocate_slots call and returns a pre-canned multi-segment cache hit.

    Cache hit layout (in blocks, block_size=10):
      seg0: blocks [0, 1]     tokens   0..19
      gap0: blocks [2]        tokens  20..29
      seg1: blocks [3, 4, 5]  tokens  30..59
      gap1: blocks [6]        tokens  60..69
      seg2: blocks [7, 8]     tokens  70..89

    Three segments separated by two gaps — exactly the shape that trips
    the segment/gap mis-pairing bug when the swallow consumes seg1 but
    iteration i=1 then treats seg2 as the "preceding" segment for gap1.
    """
    # Import inside the helper so the heavy scheduler import is only paid
    # when these regression tests actually run.
    from tests.v1.core.utils import create_requests, create_scheduler  # noqa: F401
    from vllm.v1.core.kv_cache_manager import (  # noqa: F401
        KVCacheBlocks,
        KVCacheBlockSegment,
        KVCacheManager,
    )

    class _RecordingManager(KVCacheManager):
        def __init__(self):
            # Bypass KVCacheManager.__init__ — we don't need the real pool.
            self.block_size = block_size
            self.empty_kv_cache_blocks = MagicMock(spec=KVCacheBlocks)
            self.empty_kv_cache_blocks.blocks = ([],)
            self.enable_caching = True
            self.log_stats = False
            self.block_pool = MagicMock()
            self.calls: list[dict] = []  # every allocate_slots call

            # One tagged KVCacheBlocks per segment so we can check identity.
            def _seg_blocks(tag):
                kb = MagicMock(spec=KVCacheBlocks)
                kb.blocks = ([],)
                kb._tag = tag
                return kb

            self._seg0 = KVCacheBlockSegment(
                blocks=_seg_blocks("seg0"),
                start_block_index=0,
                length_in_blocks=2,
            )
            self._seg1 = KVCacheBlockSegment(
                blocks=_seg_blocks("seg1"),
                start_block_index=3,
                length_in_blocks=3,
            )
            self._seg2 = KVCacheBlockSegment(
                blocks=_seg_blocks("seg2"),
                start_block_index=7,
                length_in_blocks=2,
            )

        def get_computed_blocks(self, request):
            return [self._seg0, self._seg1, self._seg2]

        def allocate_slots(self, request, num_new_tokens, **kwargs):
            new_computed = kwargs.get("new_computed_blocks")
            tag = getattr(new_computed, "_tag", None) if new_computed else None
            self.calls.append(
                {
                    "num_new_tokens": num_new_tokens,
                    "num_new_computed_tokens": kwargs.get(
                        "num_new_computed_tokens", 0),
                    "new_computed_tag": tag,
                    "request_num_computed_tokens": request.num_computed_tokens,
                }
            )
            return MagicMock(spec=KVCacheBlocks)

        def get_num_common_prefix_blocks(self, *args, **kwargs):
            return [0]

        def get_blocks(self, request_id):
            return self.empty_kv_cache_blocks

        def free(self, request):
            pass

        def touch_computed_segments(self, request):
            pass

        def release_computed_segment(self, seg):
            pass

    scheduler = create_scheduler(
        block_size=block_size,
        enable_prefix_caching=True,
        skip_tokenizer_init=True,
    )
    manager = _RecordingManager()
    scheduler.kv_cache_manager = manager
    scheduler.enable_hole_fill = True
    return scheduler, manager


@patch("vllm.config.model.get_config")
@patch("vllm.config.model._get_and_verify_dtype")
@patch("vllm.config.model.get_hf_image_processor_config")
@patch("vllm.config.model.get_hf_text_config")
def test_hole_fill_does_not_relink_following_segment(
    mock_get_text_config,
    mock_get_image_config,
    mock_get_dtype,
    mock_get_config,
):
    """Regression test for the `_allocate_slots_for_holes` mis-pairing bug.

    After iteration i=0's swallow consumes seg1, iteration i=1 must NOT
    pass `seg2.blocks` as `new_computed_blocks` of its main
    `allocate_slots` call — seg2 FOLLOWS gap1 and is the swallow target,
    not the preceding segment. The fix makes iteration i>=1 pass empty
    new_computed_blocks and lets the swallow at the end link the
    following segment.

    We assert the exact call pattern:
      1.  allocate_slots(num_new_tokens=gap0_len, new_computed=seg0)  # i=0
      2.  allocate_slots(num_new_tokens=0,       new_computed=seg1)   # swallow
      3.  allocate_slots(num_new_tokens=gap1_len, new_computed=None)  # i=1
      4.  allocate_slots(num_new_tokens=0,       new_computed=seg2)   # swallow

    Before the fix, call #3 had new_computed=seg2 (wrong segment
    linked at wrong offset), which also suppressed call #4.
    """
    mock_text_config = SimpleNamespace(
        model_type="opt",
        max_position_embeddings=2048,
        hidden_size=768,
        num_hidden_layers=12,
        num_attention_heads=12,
        num_key_value_heads=12,
        vocab_size=50272,
    )
    mock_hf_config = SimpleNamespace(
        model_type="opt",
        is_encoder_decoder=False,
        architectures=["OPTForCausalLM"],
        get_text_config=lambda: mock_text_config,
    )
    mock_get_config.return_value = mock_hf_config
    mock_get_dtype.return_value = torch.float16
    mock_get_image_config.return_value = None
    mock_get_text_config.return_value = mock_text_config

    from tests.v1.core.utils import create_requests

    block_size = 10
    scheduler, manager = _make_hole_fill_scheduler(block_size=block_size)

    # Prompt must extend at least to the end of the last segment so
    # hole-fill can cover all three segments in one shot.
    reqs = create_requests(num_requests=1, num_tokens=90)
    req = reqs[0]
    scheduler.add_request(req)

    output = scheduler.schedule()

    # Hole fill should have fired with the two real gaps, scheduling
    # gap0 (10 tokens) + gap1 (10 tokens).
    assert req.request_id in output.hole_fill_gap_ranges, (
        "expected hole fill to engage with two gaps")
    assert output.hole_fill_gap_ranges[req.request_id] == [(20, 10), (60, 10)]

    # Inspect the exact pattern of allocate_slots calls made by
    # _allocate_slots_for_holes.
    calls = manager.calls
    # Expect 4 calls in this order.
    assert len(calls) == 4, (
        f"expected 4 allocate_slots calls, got {len(calls)}: {calls}")

    # Call 1: i=0 main call, preceding = seg0.
    assert calls[0]["num_new_tokens"] == 10
    assert calls[0]["new_computed_tag"] == "seg0", (
        f"i=0 should link seg0 as preceding; got {calls[0]!r}")

    # Call 2: i=0 swallow of seg1.
    assert calls[1]["num_new_tokens"] == 0
    assert calls[1]["new_computed_tag"] == "seg1", (
        f"i=0 swallow should link seg1; got {calls[1]!r}")

    # Call 3: i=1 main call — must NOT relink seg2 here.
    assert calls[2]["num_new_tokens"] == 10
    assert calls[2]["new_computed_tag"] is None, (
        "i=1 must pass empty new_computed_blocks — the bug was linking "
        f"segments[seg_idx] as 'preceding' here. Got {calls[2]!r}")

    # Call 4: i=1 swallow of seg2 (only reachable when call 3 was correct).
    assert calls[3]["num_new_tokens"] == 0
    assert calls[3]["new_computed_tag"] == "seg2", (
        f"i=1 swallow should link seg2; got {calls[3]!r}")


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__]))
