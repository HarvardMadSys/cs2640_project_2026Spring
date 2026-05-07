"""Tests for the core data model."""

from adaptive_cache.types import Block, BlockType, Zone


def test_block_creation():
    block = Block(
        block_id=0,
        block_type=BlockType.SYSTEM,
        content="You are a helpful assistant.",
        token_count=6,
        step_created=0,
    )
    assert block.block_id == 0
    assert block.block_type == BlockType.SYSTEM
    assert block.zone == Zone.SUFFIX  # default


def test_pin_score():
    block = Block(
        block_id=1,
        block_type=BlockType.OBS_FILE,
        content="def foo(): pass",
        token_count=10,
        step_created=1,
        importance=0.8,
        stability=0.7,
    )
    assert block.pin_score == 0.8 * 0.7


def test_evict_priority():
    block = Block(
        block_id=2,
        block_type=BlockType.OBS_ERROR,
        content="Error: file not found",
        token_count=5,
        step_created=1,
        importance=0.5,
        stability=0.1,
    )
    # evict_priority = importance * (1 - stability) = 0.5 * 0.9 = 0.45
    assert abs(block.evict_priority - 0.45) < 1e-6


def test_importance_variance_new_block():
    block = Block(
        block_id=3,
        block_type=BlockType.THOUGHT,
        content="Let me think...",
        token_count=4,
        step_created=0,
    )
    # New block with no history → high variance (1.0)
    assert block.importance_variance() == 1.0


def test_importance_variance_stable():
    block = Block(
        block_id=4,
        block_type=BlockType.OBS_FILE,
        content="class Foo: pass",
        token_count=8,
        step_created=0,
        importance_history=[0.7, 0.7, 0.7, 0.7, 0.7],
    )
    assert block.importance_variance() == 0.0


def test_importance_variance_volatile():
    block = Block(
        block_id=5,
        block_type=BlockType.OBS_ERROR,
        content="Error!",
        token_count=3,
        step_created=0,
        importance_history=[0.9, 0.1, 0.9, 0.1],
    )
    # Should have high variance
    assert block.importance_variance() > 0.1
