"""Tests for the importance x stability scoring pipeline."""

from adaptive_cache.config import CacheConfig
from adaptive_cache.scorer import Scorer, IMPORTANCE_PRIOR, STABILITY_PRIOR
from adaptive_cache.types import Block, BlockType, Zone


def _make_block(block_type: BlockType, content: str = "", step: int = 0, **kwargs) -> Block:
    return Block(
        block_id=kwargs.get("block_id", 0),
        block_type=block_type,
        content=content,
        token_count=10,
        step_created=step,
        **{k: v for k, v in kwargs.items() if k != "block_id"},
    )


def test_structural_prior_ordering():
    """System > Task > OBS_FILE > OBS_ERROR > OBS_SHELL."""
    assert IMPORTANCE_PRIOR[BlockType.SYSTEM] >= IMPORTANCE_PRIOR[BlockType.OBS_FILE]
    assert IMPORTANCE_PRIOR[BlockType.OBS_FILE] >= IMPORTANCE_PRIOR[BlockType.OBS_SHELL]
    assert STABILITY_PRIOR[BlockType.SYSTEM] > STABILITY_PRIOR[BlockType.OBS_ERROR]


def test_scorer_basic():
    config = CacheConfig()
    scorer = Scorer(config)

    blocks = [
        _make_block(BlockType.SYSTEM, "You are an agent.", block_id=0),
        _make_block(BlockType.OBS_FILE, "def foo(): pass", block_id=1),
        _make_block(BlockType.OBS_ERROR, "Error: not found", block_id=2),
    ]

    scorer.score_blocks(blocks, step=1)

    # System should score highest on both axes
    assert blocks[0].importance > blocks[2].importance
    assert blocks[0].stability > blocks[2].stability

    # File should be more stable than error
    assert blocks[1].stability > blocks[2].stability


def test_reference_count_boosts_importance():
    config = CacheConfig()
    scorer = Scorer(config)

    block_referenced = _make_block(
        BlockType.OBS_FILE, "def parse_input(data): return data.split()", block_id=0
    )
    block_unreferenced = _make_block(
        BlockType.OBS_FILE, "def unused_helper(): pass", block_id=1
    )

    # Score with content that references parse_input
    recent = "I need to check parse_input to understand the bug"
    scorer.score_blocks([block_referenced, block_unreferenced], step=1, all_content=recent)

    # Referenced block should have higher importance
    assert block_referenced.importance >= block_unreferenced.importance
    assert block_referenced.reference_count > 0


def test_importance_history_tracked():
    config = CacheConfig()
    scorer = Scorer(config)

    block = _make_block(BlockType.OBS_FILE, "class Foo: pass")

    for step in range(5):
        scorer.score_blocks([block], step=step)

    assert len(block.importance_history) == 5


def test_variance_stability():
    """A block with consistent importance should have high stability."""
    config = CacheConfig()
    scorer = Scorer(config)

    stable_block = _make_block(BlockType.OBS_FILE, "def stable(): pass")
    stable_block.importance_history = [0.6, 0.6, 0.6, 0.6, 0.6]

    volatile_block = _make_block(BlockType.OBS_ERROR, "Error!")
    volatile_block.importance_history = [0.9, 0.1, 0.8, 0.2, 0.7]

    scorer.score_blocks([stable_block, volatile_block], step=5)

    assert stable_block.stability > volatile_block.stability
