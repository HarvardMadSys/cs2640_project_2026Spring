"""AdaptiveCache: main middleware entry point.

Sits between the agent loop and the LLM API. At each step boundary:
  1. Segment new content into blocks
  2. Score all blocks (importance x stability)
  3. Assign zones
  4. Evict if over budget (hole-leaving)
  5. Conditionally reorganize layout
  6. Return reconstructed message list for LLM API
"""

from __future__ import annotations

from adaptive_cache.config import CacheConfig
from adaptive_cache.evictor import Evictor
from adaptive_cache.layout import LayoutOptimizer
from adaptive_cache.scorer import Scorer
from adaptive_cache.segmenter import segment_messages, segment_new_step
from adaptive_cache.types import Block, BlockType, Zone

# Import lazily so cache.py doesn't require harness at test time
try:
    from harness.attention_hook import MockAttentionHook as _MockAttentionHook
except ImportError:
    _MockAttentionHook = None


class AdaptiveCache:
    """Context manager middleware for LLM agents.

    Usage:
        cache = AdaptiveCache()

        # Initialize with system prompt + task
        cache.init(system_prompt, task_description)

        # Each agent step:
        messages = cache.update(thought, action, observation, tool_name)
        response = llm(messages)
    """

    def __init__(self, config: CacheConfig | None = None) -> None:
        self.config = config or CacheConfig()
        self.scorer = Scorer(self.config)
        self.evictor = Evictor(self.config)
        self.layout = LayoutOptimizer(self.config)

        self.blocks: list[Block] = []
        self.step: int = 0
        self._next_block_id: int = 0
        self._total_allocated: int = 0  # includes holes
        self._reorg_count: int = 0

        # Attention hook: populated with MockAttentionHook by default.
        # Swap in VLLMAttentionHook when running with --attention-backend EAGER.
        # Set to None to disable (useful in tests or when w_cumulative_attention=0).
        if _MockAttentionHook is not None and self.config.w_cumulative_attention > 0:
            self._attention_hook = _MockAttentionHook(
                sink_positions=self.config.sink_positions
            )
        else:
            self._attention_hook = None

    def init(self, system_prompt: str, task: str) -> list[dict]:
        """Initialize with system prompt and task description.

        Returns the initial message list for the first LLM call.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task},
        ]
        self.blocks = segment_messages(messages, step=0, start_block_id=0)
        self._next_block_id = len(self.blocks)

        # System and task are always pinned
        for block in self.blocks:
            block.zone = Zone.PIN
            block.pinned_since_step = 0

        self._total_allocated = sum(b.token_count for b in self.blocks)
        return messages

    def update(
        self,
        thought: str,
        action: dict | None = None,
        observation: str | None = None,
        tool_name: str | None = None,
    ) -> list[dict]:
        """Process a new agent step and return the context for the next LLM call.

        This is the 6-step pipeline from system-design.md §2.

        Args:
            thought: The agent's reasoning for this step.
            action: Tool call dict ({"function": {"name": ..., "arguments": ...}}).
            observation: Tool result string.
            tool_name: Name of the tool that produced the observation.

        Returns:
            OpenAI-format message list for the next LLM call.
        """
        self.step += 1

        # --- Step 1: Block Segmentation ---
        new_blocks = segment_new_step(
            thought=thought,
            action=action,
            observation=observation,
            step=self.step,
            start_block_id=self._next_block_id,
            tool_name=tool_name,
        )
        self.blocks.extend(new_blocks)
        self._next_block_id += len(new_blocks)
        new_tokens = sum(b.token_count for b in new_blocks)
        self._total_allocated += new_tokens

        # Recent content for reference counting
        recent_content = thought
        if observation:
            recent_content += " " + observation

        # --- Step 2a: Update attention history (before scoring reads it) ---
        if self._attention_hook is not None:
            seq_len = sum(b.token_count for b in self.blocks)
            # Compute cumulative token positions for each block
            block_positions: dict[int, tuple[int, int]] = {}
            offset = 0
            for b in self.blocks:
                block_positions[b.block_id] = (offset, offset + b.token_count)
                offset += b.token_count
            self._attention_hook.update_block_attention(
                self.blocks, block_positions, seq_len
            )

        # --- Step 2b: Scoring Pipeline ---
        self.scorer.score_blocks(self.blocks, self.step, recent_content)

        # --- Step 3: Zone Assignment ---
        self.layout.assign_zones(self.blocks, self.step)

        # --- Step 4: Eviction Engine ---
        current_tokens = sum(b.token_count for b in self.blocks if b.zone != Zone.EVICT)
        surviving = self.evictor.evict(self.blocks, current_tokens)
        self.blocks = surviving

        # --- Step 5: Layout Optimizer (conditional) ---
        hole_ratio = self.evictor.compute_hole_ratio(self.blocks, self._total_allocated)
        if self.layout.should_reorganize(self.blocks, hole_ratio):
            self.blocks = self.layout.reorganize(self.blocks)
            self._reorg_count += 1
            # After reorg, reset total_allocated to actual surviving tokens
            self._total_allocated = sum(b.token_count for b in self.blocks)

        # --- Step 6: Prompt Construction ---
        return self.to_messages()

    def to_messages(self) -> list[dict]:
        """Reconstruct OpenAI-format messages from the current block list.

        The message list preserves the three-zone layout:
          Zone 1 (PIN): system prompt + pinned blocks at the prefix
          Zone 2 (MIDDLE): moderate-score blocks
          Zone 3 (SUFFIX): recent volatile blocks at the end
        """
        messages: list[dict] = []

        for block in self.blocks:
            if block.zone == Zone.EVICT:
                continue

            msg = _block_to_message(block)
            if msg is not None:
                messages.append(msg)

        return messages

    @property
    def total_tokens(self) -> int:
        return sum(b.token_count for b in self.blocks if b.zone != Zone.EVICT)

    @property
    def pinned_tokens(self) -> int:
        return sum(b.token_count for b in self.blocks if b.zone == Zone.PIN)

    @property
    def stats(self) -> dict:
        """Current cache statistics."""
        zones = {z: [] for z in Zone}
        for b in self.blocks:
            zones[b.zone].append(b)

        return {
            "step": self.step,
            "total_tokens": self.total_tokens,
            "pinned_tokens": self.pinned_tokens,
            "middle_tokens": sum(b.token_count for b in zones[Zone.MIDDLE]),
            "suffix_tokens": sum(b.token_count for b in zones[Zone.SUFFIX]),
            "num_blocks": len(self.blocks),
            "num_pinned": len(zones[Zone.PIN]),
            "num_middle": len(zones[Zone.MIDDLE]),
            "num_suffix": len(zones[Zone.SUFFIX]),
            "reorg_count": self._reorg_count,
            "cache_hit_estimate": self.pinned_tokens / max(self.total_tokens, 1),
        }


def _block_to_message(block: Block) -> dict | None:
    """Convert a Block back to an OpenAI-format message dict."""
    if block.block_type == BlockType.SYSTEM:
        return {"role": "system", "content": block.content}

    if block.block_type == BlockType.TASK:
        return {"role": "user", "content": block.content}

    if block.block_type == BlockType.THOUGHT:
        return {"role": "assistant", "content": block.content}

    if block.block_type == BlockType.ACTION:
        # Reconstruct as assistant message with tool_call
        # For simplicity, embed the action as assistant content
        return {"role": "assistant", "content": f"[Action: {block.content}]"}

    if block.block_type.value.startswith("obs_"):
        return {"role": "user", "content": f"[Observation]\n{block.content}"}

    return {"role": "user", "content": block.content}
