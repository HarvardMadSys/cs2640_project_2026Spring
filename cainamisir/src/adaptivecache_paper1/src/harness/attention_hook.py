"""Attention hooks for AdaptiveCache.

Two implementations:

1. MockAttentionHook (default): simulates attention using structural position
   priors. Available immediately, no model access required.

2. VLLMAttentionHook: registers PyTorch forward hooks on vLLM attention layers
   to capture real attention weights. Only works when vLLM is running with
   --attention-backend EAGER (Flash Attention does not expose the attention
   matrix). This is a server-side hook — it runs inside Modal.

Usage (local / testing):
    hook = MockAttentionHook()
    attention_by_block = hook.aggregate_by_block(seq_len=128, block_positions={0: (0, 32), 1: (32, 64)})

Usage (server-side with EAGER backend):
    hook = VLLMAttentionHook(model)
    hook.register()
    # ... run forward pass ...
    attention_by_block = hook.aggregate_by_block(attention_weights, block_positions)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class AttentionRecord:
    """Per-block attention log entry."""

    instance_id: str
    step: int
    block_id: int
    token_position: int
    attention_received: float
    layer: int
    head: int


# ---------------------------------------------------------------------------
# MockAttentionHook: structural position-based attention estimate
# ---------------------------------------------------------------------------


class MockAttentionHook:
    """Simulates attention using structural priors — no model access required.

    Uses two heuristics that approximate real attention distributions:

    1. Recency bias: tokens near the end of the prompt receive more attention.
    2. Sink bias: the first few token positions (attention sinks, per StreamingLLM)
       always receive disproportionate attention.

    These are coarse approximations but directionally correct for importance
    scoring: recent tokens and early anchor tokens tend to receive the most
    attention in autoregressive language models.
    """

    def __init__(self, sink_positions: int = 4, recency_weight: float = 0.7):
        """
        Args:
            sink_positions: Number of "attention sink" token positions at the start.
            recency_weight: How much weight to give recency vs. sink bias [0, 1].
        """
        self.sink_positions = sink_positions
        self.recency_weight = recency_weight

    def aggregate_by_block(
        self,
        seq_len: int,
        block_positions: Dict[int, Tuple[int, int]],
    ) -> Dict[int, float]:
        """Compute structural attention estimate per block.

        Args:
            seq_len: Total sequence length (in tokens).
            block_positions: {block_id: (token_start, token_end)}.

        Returns:
            {block_id: estimated_attention_score in [0, 1]}
        """
        if seq_len == 0:
            return {bid: 0.0 for bid in block_positions}

        result: Dict[int, float] = {}
        for block_id, (start, end) in block_positions.items():
            if end <= start:
                result[block_id] = 0.0
                continue

            block_len = end - start
            score = 0.0

            for pos in range(start, end):
                # Sink score: first sink_positions tokens get full credit
                if pos < self.sink_positions:
                    sink_score = 1.0
                else:
                    sink_score = 0.0

                # Recency score: linearly increasing toward end of prompt
                recency_score = pos / max(seq_len - 1, 1)

                pos_score = (
                    self.recency_weight * recency_score
                    + (1.0 - self.recency_weight) * sink_score
                )
                score += pos_score

            result[block_id] = score / block_len

        return result

    def update_block_attention(
        self,
        blocks,  # list[Block]
        block_positions: Dict[int, Tuple[int, int]],
        seq_len: int,
    ) -> None:
        """Compute and append mock attention to each block's attention_history.

        Args:
            blocks: List of Block objects.
            block_positions: {block_id: (token_start, token_end)}.
            seq_len: Total sequence length.
        """
        scores = self.aggregate_by_block(seq_len, block_positions)
        for block in blocks:
            if block.block_id in scores:
                block.attention_history.append(scores[block.block_id])


# ---------------------------------------------------------------------------
# VLLMAttentionHook: real attention via PyTorch forward hooks (EAGER backend)
# ---------------------------------------------------------------------------


class VLLMAttentionHook:
    """Captures real attention weights from vLLM's attention layers.

    Requirements:
    - vLLM must run with --attention-backend EAGER (not Flash Attention).
    - This hook is registered server-side inside modal_app/serve.py.
    - Flash Attention (the default) does not materialise the attention matrix,
      so real capture is not possible without switching backends.

    Usage:
        hook = VLLMAttentionHook(llm.llm_engine.model_executor.driver_worker.model_runner.model)
        hook.register()
        # Run vLLM generate() ...
        if hook.has_data():
            scores = hook.aggregate_by_block(block_positions)
    """

    def __init__(self, model=None):
        self.model = model
        self._handles: list = []
        self._captured_weights: list = []  # list of (layer_idx, attn_weights_tensor)
        self._active = False

    def register(self) -> None:
        """Register forward hooks on all attention layers.

        Raises:
            RuntimeError: If the model is None or has no attention layers.
        """
        if self.model is None:
            raise RuntimeError(
                "VLLMAttentionHook requires a model object. "
                "Pass the vLLM model via VLLMAttentionHook(model=llm_model)."
            )

        try:
            import torch.nn as nn
        except ImportError:
            raise RuntimeError("PyTorch is required for VLLMAttentionHook.")

        self._handles.clear()
        self._captured_weights.clear()
        layer_idx = 0

        for name, module in self.model.named_modules():
            # vLLM attention layers are typically named "attn" or contain "attention"
            if "attn" in name.lower() and hasattr(module, "forward"):
                idx = layer_idx  # capture for closure

                def make_hook(layer_i):
                    def hook_fn(module, input, output):
                        # In EAGER mode, vLLM attention layers return
                        # (attn_output, attn_weights) or just attn_output.
                        # Attempt to capture the second return value.
                        if isinstance(output, (tuple, list)) and len(output) >= 2:
                            weights = output[1]
                            if weights is not None:
                                self._captured_weights.append(
                                    (layer_i, weights.detach().cpu())
                                )
                    return hook_fn

                handle = module.register_forward_hook(make_hook(idx))
                self._handles.append(handle)
                layer_idx += 1

        if not self._handles:
            raise RuntimeError(
                "No attention layers found in model. "
                "Make sure you are using --attention-backend EAGER."
            )

        self._active = True

    def deregister(self) -> None:
        """Remove all registered hooks."""
        for handle in self._handles:
            handle.remove()
        self._handles.clear()
        self._active = False

    def has_data(self) -> bool:
        """True if attention weights were captured on the last forward pass."""
        return bool(self._captured_weights)

    def clear(self) -> None:
        """Clear captured weights (call after each step)."""
        self._captured_weights.clear()

    def aggregate_by_block(
        self,
        block_positions: Dict[int, Tuple[int, int]],
    ) -> Dict[int, float]:
        """Aggregate captured attention weights into per-block scores.

        For each block, compute the mean attention weight that tokens
        *after* the block assign to tokens *within* the block. This
        measures how much the model "looks back" at each block when
        generating subsequent tokens.

        Args:
            block_positions: {block_id: (token_start, token_end)}.

        Returns:
            {block_id: mean_attention_received}. Returns zeros if no data.
        """
        if not self._captured_weights:
            return {bid: 0.0 for bid in block_positions}

        try:
            import torch

            # Average attention across all captured layers
            # Shape of each tensor: (batch, heads, query_len, key_len)
            all_scores: Dict[int, list] = {bid: [] for bid in block_positions}

            for _layer_idx, attn_weights in self._captured_weights:
                if attn_weights.dim() != 4:
                    continue
                # Mean over batch and heads: (query_len, key_len)
                mean_attn = attn_weights.mean(dim=(0, 1))  # (Q, K)

                for block_id, (start, end) in block_positions.items():
                    if end <= start or end > mean_attn.size(1):
                        continue
                    # Attention received = mean attention FROM all positions
                    # TO tokens in [start, end)
                    received = mean_attn[:, start:end].mean().item()
                    all_scores[block_id].append(received)

            return {
                bid: (sum(scores) / len(scores)) if scores else 0.0
                for bid, scores in all_scores.items()
            }

        except Exception:
            return {bid: 0.0 for bid in block_positions}


# ---------------------------------------------------------------------------
# Backward-compatible alias (existing code imports AttentionHook)
# ---------------------------------------------------------------------------


class AttentionHook(MockAttentionHook):
    """Default attention hook — uses structural position priors.

    This replaces the old stub with a working implementation. For real
    attention capture, use VLLMAttentionHook (requires EAGER backend).
    """

    def __init__(self, output_dir: str = "/tmp/attention_logs", **kwargs):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        super().__init__(**kwargs)

    def log_attention(
        self,
        instance_id: str,
        step: int,
        block_positions: Dict[int, Tuple[int, int]],
        seq_len: int,
    ) -> None:
        """Compute and log mock attention to JSONL file."""
        import json

        scores = self.aggregate_by_block(seq_len, block_positions)
        log_path = self.output_dir / f"{instance_id}.jsonl"
        with open(log_path, "a") as f:
            for block_id, score in scores.items():
                start, end = block_positions.get(block_id, (0, 0))
                record = AttentionRecord(
                    instance_id=instance_id,
                    step=step,
                    block_id=block_id,
                    token_position=start,
                    attention_received=score,
                    layer=-1,  # mock — not layer-specific
                    head=-1,
                )
                f.write(json.dumps(record.__dict__) + "\n")
