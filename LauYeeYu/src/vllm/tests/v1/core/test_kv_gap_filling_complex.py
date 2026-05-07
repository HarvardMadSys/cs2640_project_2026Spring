# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import torch

from vllm.v1.core.kv_cache_manager import (KVCacheBlocks, KVCacheBlockSegment,
                                           KVCacheManager)
from vllm.v1.core.sched.scheduler import Scheduler
from vllm.v1.request import Request

from .utils import create_requests, create_scheduler


class MockKVCacheManager(KVCacheManager):
    """
    A customized KVCacheManager that returns specific disjoint segments
    to test the scheduler's gap-filling logic.
    """
    def __init__(self, *args, **kwargs):
        # We don't call super().__init__ because we want to avoid 
        # initializing the real coordinator/pool for this unit test.
        self.block_size = kwargs.get("hash_block_size", 10)
        self.empty_kv_cache_blocks = MagicMock(spec=KVCacheBlocks)
        self.empty_kv_cache_blocks.blocks = ([],)
        self.enable_caching = True
        self.log_stats = False
        self.block_pool = MagicMock()

    def get_computed_blocks(self, request: Request) -> list[KVCacheBlockSegment]:
        """
        Returns segments with two holes:
        Seg 1: Blocks 0 (tokens 0-9)
        Gap 1: Block 1 (tokens 10-19)
        Seg 2: Block 2 (tokens 20-29)
        Gap 2: Block 3 (tokens 30-39)
        Seg 3: Block 4 (tokens 40-49)
        """
        blocks1 = MagicMock(spec=KVCacheBlocks)
        blocks1.blocks = ([],)
        blocks2 = MagicMock(spec=KVCacheBlocks)
        blocks2.blocks = ([],)
        blocks3 = MagicMock(spec=KVCacheBlocks)
        blocks3.blocks = ([],)

        seg1 = KVCacheBlockSegment(blocks=blocks1, start_block_index=0, length_in_blocks=1)
        seg2 = KVCacheBlockSegment(blocks=blocks2, start_block_index=2, length_in_blocks=1)
        seg3 = KVCacheBlockSegment(blocks=blocks3, start_block_index=4, length_in_blocks=1)

        return [seg1, seg2, seg3]

    def allocate_slots(self, request, num_new_tokens, **kwargs) -> KVCacheBlocks:
        # Just return a mock block result
        return MagicMock(spec=KVCacheBlocks)

    def get_num_common_prefix_blocks(self, *args, **kwargs):
        return [0]

    def get_blocks(self, request_id: str):
        return self.empty_kv_cache_blocks

    def free(self, request):
        pass

@patch("vllm.config.model.get_config")
@patch("vllm.config.model._get_and_verify_dtype")
@patch("vllm.config.model.get_hf_image_processor_config")
@patch("vllm.config.model.get_hf_text_config")
def test_scheduler_multi_round_gap_filling(mock_get_text_config, mock_get_image_config, mock_get_dtype, mock_get_config):
    # Text config should have numeric values for size/length fields
    mock_text_config = SimpleNamespace(
        model_type="opt",
        max_position_embeddings=2048,
        hidden_size=768,
        num_hidden_layers=12,
        num_attention_heads=12,
        num_key_value_heads=12,
        vocab_size=50272,
    )

    # Mock configs
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

    block_size = 10
    scheduler = create_scheduler(block_size=block_size, enable_prefix_caching=True, skip_tokenizer_init=True)

    # Inject our mock manager and enable hole filling for this test.
    mock_manager = MockKVCacheManager(hash_block_size=block_size)
    scheduler.kv_cache_manager = mock_manager
    scheduler.enable_hole_fill = True

    # Create a request with enough tokens to cover all segments and holes
    prompt_tokens = [i for i in range(100)]
    requests = create_requests(num_requests=1, num_tokens=100)
    req = requests[0]
    scheduler.add_request(req)

    # --- Round 1 (one-step hole filling) ---
    # 1. WAITING loop calls get_computed_blocks.
    # 2. Seg 1 (start=0) is used as prefix. num_computed_tokens = 10.
    # 3. Seg 2 and Seg 3 are stored in req.computed_segments.
    # 4. One-step hole fill: computes gap ranges [(10,10), (30,10), (50,50)]
    #    Total gap tokens = 70, which fits in budget.
    # 5. Allocates all segments+gaps at once and schedules 70 tokens.
    output1 = scheduler.schedule()

    # One-step hole fill schedules ALL gap tokens at once.
    assert output1.num_scheduled_tokens[req.request_id] == 70
    assert req.request_id in output1.hole_fill_gap_ranges
    assert output1.hole_fill_gap_ranges[req.request_id] == [
        (10, 10), (30, 10), (50, 50)]

    # After Round 1 finishes, _update_after_schedule advances past all gaps:
    # num_computed_tokens = 50 + 50 = 100 (end of last gap).
    # All segments are consumed.
    assert req.num_computed_tokens == 100
    assert len(req.computed_segments) == 0

if __name__ == "__main__":
    import sys
    pytest.main([__file__])
