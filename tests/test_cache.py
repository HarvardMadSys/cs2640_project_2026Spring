"""Integration tests: full AdaptiveCache pipeline on mock agent traces."""

from adaptive_cache.cache import AdaptiveCache
from adaptive_cache.config import CacheConfig
from adaptive_cache.types import BlockType, Zone


def test_init():
    cache = AdaptiveCache()
    messages = cache.init("You are an agent.", "Fix the bug in parser.py")

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert len(cache.blocks) == 2
    assert all(b.zone == Zone.PIN for b in cache.blocks)


def test_single_step():
    cache = AdaptiveCache()
    cache.init("You are an agent.", "Fix the bug")

    messages = cache.update(
        thought="I should search for the error pattern.",
        action={"function": {"name": "grep", "arguments": '{"pattern": "error"}'}},
        observation="src/parser.py:42: raise ValueError('parse error')",
        tool_name="grep",
    )

    assert cache.step == 1
    assert len(cache.blocks) > 2  # system + task + thought + action + observation

    # System and task should be pinned
    sys_blocks = [b for b in cache.blocks if b.block_type == BlockType.SYSTEM]
    task_blocks = [b for b in cache.blocks if b.block_type == BlockType.TASK]
    assert all(b.zone == Zone.PIN for b in sys_blocks)
    assert all(b.zone == Zone.PIN for b in task_blocks)


def test_multi_step_trace():
    """3-step trace simulating a SWE-bench fix."""
    cache = AdaptiveCache()
    cache.init("You are a coding agent.", "Fix the TypeError in utils/parser.py")

    # Step 1: grep for the error
    cache.update(
        thought="Let me search for TypeError in the codebase.",
        action={"function": {"name": "grep", "arguments": '{"pattern": "TypeError"}'}},
        observation="utils/parser.py:42: raise TypeError('unexpected type')\nutils/parser.py:58: except TypeError:",
        tool_name="grep",
    )

    step1_stats = cache.stats
    assert step1_stats["num_pinned"] >= 2  # at least system + task

    # Step 2: read the file
    cache.update(
        thought="I need to read parser.py to understand the function.",
        action={"function": {"name": "file_read", "arguments": '{"path": "utils/parser.py"}'}},
        observation="def parse_input(data):\n    if not isinstance(data, str):\n        raise TypeError('unexpected type')\n    return data.split()\n",
        tool_name="file_read",
    )

    step2_stats = cache.stats
    assert step2_stats["total_tokens"] > step1_stats["total_tokens"]

    # Step 3: write the fix
    cache.update(
        thought="The fix is to handle the case where data is not a string by converting it.",
        action={"function": {"name": "file_write", "arguments": '{"path": "utils/parser.py"}'}},
        observation="Written 120 chars to utils/parser.py",
        tool_name="file_write",
    )

    step3_stats = cache.stats
    assert step3_stats["step"] == 3
    assert step3_stats["cache_hit_estimate"] > 0  # some tokens should be pinned


def test_eviction_under_pressure():
    """With a very low budget, eviction should fire."""
    config = CacheConfig(soft_budget=500, hard_budget=600)
    cache = AdaptiveCache(config)
    cache.init("System prompt.", "Task.")

    # Add many steps to exceed budget
    for i in range(10):
        cache.update(
            thought=f"Thinking about step {i}. " * 10,
            action={"function": {"name": "bash", "arguments": f'{{"command": "echo {i}"}}'}},
            observation=f"Output of step {i}\n" * 20,
            tool_name="bash",
        )

    # Total tokens should be under budget
    assert cache.total_tokens <= config.soft_budget + 200  # some slack for block boundaries

    # System and task should survive eviction
    sys_blocks = [b for b in cache.blocks if b.block_type == BlockType.SYSTEM]
    task_blocks = [b for b in cache.blocks if b.block_type == BlockType.TASK]
    assert len(sys_blocks) >= 1
    assert len(task_blocks) >= 1


def test_prefix_stability():
    """Pinned prefix should be identical across steps (when no reorg)."""
    cache = AdaptiveCache()
    cache.init("System prompt for testing.", "Fix the bug in parser.py line 42.")

    # Step 1
    cache.update(
        thought="Reading the file.",
        action={"function": {"name": "file_read", "arguments": '{"path": "parser.py"}'}},
        observation="def parse(x): return x.split()",
        tool_name="file_read",
    )
    pinned_1 = [
        (b.block_id, b.content)
        for b in cache.blocks
        if b.zone == Zone.PIN
    ]

    # Step 2
    cache.update(
        thought="Now running tests.",
        action={"function": {"name": "bash", "arguments": '{"command": "pytest"}'}},
        observation="1 passed, 0 failed",
        tool_name="bash",
    )
    pinned_2 = [
        (b.block_id, b.content)
        for b in cache.blocks
        if b.zone == Zone.PIN
    ]

    # The pinned prefix should be a superset (may grow, but originals stay)
    pinned_1_ids = {bid for bid, _ in pinned_1}
    pinned_2_ids = {bid for bid, _ in pinned_2}
    assert pinned_1_ids.issubset(pinned_2_ids), (
        f"Pinned blocks from step 1 should still be pinned in step 2. "
        f"Lost: {pinned_1_ids - pinned_2_ids}"
    )


def test_stats():
    cache = AdaptiveCache()
    cache.init("System.", "Task.")

    cache.update(
        thought="Thinking.",
        observation="Result.",
        tool_name="bash",
    )

    stats = cache.stats
    assert "step" in stats
    assert "total_tokens" in stats
    assert "pinned_tokens" in stats
    assert "cache_hit_estimate" in stats
    assert stats["step"] == 1
