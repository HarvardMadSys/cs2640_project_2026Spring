"""Tests for step-level context management in AdaptiveCacheModel."""

from harness.swe_model import (
    _extract_content, _parse_steps, _score_step, _classify_obs,
    AdaptiveCacheModel,
)
from adaptive_cache.types import BlockType


# --- Content extraction ---

def test_extract_content_string():
    assert _extract_content({"content": "hello"}) == "hello"

def test_extract_content_list():
    msg = {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
    assert "a" in _extract_content(msg) and "b" in _extract_content(msg)

def test_extract_content_empty():
    assert _extract_content({}) == ""
    assert _extract_content({"content": None}) == ""


# --- Step parsing ---

def _make_messages():
    """Standard 3-step conversation for testing."""
    return [
        {"role": "system", "content": "You are an agent."},
        {"role": "user", "content": "Fix the bug in parser.py"},
        # Step 1
        {"role": "assistant", "content": "Let me search.", "tool_calls": [
            {"id": "tc1", "function": {"name": "bash", "arguments": '{"cmd":"grep"}'}}
        ]},
        {"role": "tool", "content": "parser.py:42: TypeError", "tool_call_id": "tc1"},
        # Step 2
        {"role": "assistant", "content": "Reading file.", "tool_calls": [
            {"id": "tc2", "function": {"name": "bash", "arguments": '{"cmd":"cat"}'}}
        ]},
        {"role": "tool", "content": "def parse(x):\n    return x.split()", "tool_call_id": "tc2"},
        # Step 3
        {"role": "assistant", "content": "Fixing.", "tool_calls": [
            {"id": "tc3", "function": {"name": "bash", "arguments": '{"cmd":"edit"}'}}
        ]},
        {"role": "tool", "content": "File written.", "tool_call_id": "tc3"},
    ]


def test_parse_steps_count():
    steps = _parse_steps(_make_messages())
    # system + task + 3 agent steps = 5
    assert len(steps) == 5


def test_parse_steps_protected():
    steps = _parse_steps(_make_messages())
    assert steps[0].protected  # system
    assert steps[1].protected  # task
    assert not steps[2].protected  # step 1
    assert not steps[3].protected  # step 2


def test_parse_steps_msg_indices():
    steps = _parse_steps(_make_messages())
    # Step 1 should contain assistant (idx 2) + tool (idx 3)
    assert steps[2].msg_indices == [2, 3]
    # Step 2: assistant (idx 4) + tool (idx 5)
    assert steps[3].msg_indices == [4, 5]


def test_parse_steps_token_count():
    steps = _parse_steps(_make_messages())
    for step in steps:
        assert step.token_count > 0


# --- Scoring ---

def test_score_step_file_higher_than_error():
    file_step = _parse_steps(_make_messages())[3]  # "def parse(x)..." → file-like
    file_step.block_type = BlockType.OBS_FILE
    _score_step(file_step, "")

    error_step = _parse_steps(_make_messages())[2]  # "TypeError" → error-like
    error_step.block_type = BlockType.OBS_ERROR
    _score_step(error_step, "")

    assert file_step.score >= error_step.score


def test_reference_count_boosts_score():
    steps = _parse_steps(_make_messages())
    step = steps[2]
    step.block_type = BlockType.OBS_SHELL
    step.reference_count = 0
    _score_step(step, "")
    score_no_ref = step.score

    step.reference_count = 10
    _score_step(step, "")
    score_with_ref = step.score

    assert score_with_ref > score_no_ref


# --- Adaptive eviction ---

def test_adaptive_no_eviction_under_budget():
    model = AdaptiveCacheModel(
        model_name="test", cache_policy="adaptive",
        cache_budget=999_999, cost_tracking="ignore_errors",
    )
    msgs = _make_messages()
    result = model._apply_adaptive(msgs)
    assert len(result) == len(msgs)


def test_adaptive_evicts_from_middle():
    model = AdaptiveCacheModel(
        model_name="test", cache_policy="adaptive",
        cache_budget=1,  # impossibly tight — force eviction
        cost_tracking="ignore_errors",
    )
    msgs = _make_messages()
    result = model._apply_adaptive(msgs)

    # System and task must survive
    assert result[0]["role"] == "system"
    assert result[1]["role"] == "user"
    # Content may be wrapped in cache_control format
    task_content = _extract_content(result[1])
    assert "Fix the bug in parser.py" in task_content

    # Last step should survive (recency)
    assert "File written." in _extract_content(result[-1])

    # Should have fewer messages
    assert len(result) < len(msgs)


def test_adaptive_preserves_message_format():
    model = AdaptiveCacheModel(
        model_name="test", cache_policy="adaptive",
        cache_budget=999_999, cost_tracking="ignore_errors",
    )
    msgs = _make_messages()
    result = model._apply_adaptive(msgs)

    # tool_calls preserved
    assistant_msgs = [m for m in result if m.get("tool_calls")]
    assert len(assistant_msgs) == 3
    assert assistant_msgs[0]["tool_calls"][0]["id"] == "tc1"


# --- FIFO eviction ---

def test_fifo_evicts_oldest():
    model = AdaptiveCacheModel(
        model_name="test", cache_policy="fifo",
        cache_budget=20,  # tight enough to evict some steps but not all
        cost_tracking="ignore_errors",
    )
    msgs = _make_messages()  # total ~38 tokens
    result = model._apply_fifo(msgs)

    # System + task survive
    assert result[0]["role"] == "system"
    assert result[1]["content"] == "Fix the bug in parser.py"

    # Should have evicted some messages
    assert len(result) < len(msgs)
    # Protected messages (system + task = 11 tokens) + some steps kept
    assert len(result) >= 2


# --- Classify obs ---

def test_classify_obs():
    assert _classify_obs("Traceback: error") == BlockType.OBS_ERROR
    assert _classify_obs("def foo(): pass") == BlockType.OBS_FILE
    assert _classify_obs("hello world") == BlockType.OBS_SHELL
