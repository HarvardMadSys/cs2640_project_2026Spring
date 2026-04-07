"""Tests for block segmentation."""

from adaptive_cache.segmenter import segment_messages, segment_new_step, _classify_observation
from adaptive_cache.types import BlockType


def test_segment_system_and_user():
    messages = [
        {"role": "system", "content": "You are an agent."},
        {"role": "user", "content": "Fix the bug in parser.py"},
    ]
    blocks = segment_messages(messages, step=0)
    assert len(blocks) == 2
    assert blocks[0].block_type == BlockType.SYSTEM
    assert blocks[1].block_type == BlockType.TASK
    assert blocks[0].block_id == 0
    assert blocks[1].block_id == 1


def test_segment_assistant_with_thought():
    messages = [
        {"role": "assistant", "content": "I need to read the file first."},
    ]
    blocks = segment_messages(messages, step=1, start_block_id=10)
    assert len(blocks) == 1
    assert blocks[0].block_type == BlockType.THOUGHT
    assert blocks[0].block_id == 10


def test_segment_assistant_with_tool_call():
    messages = [
        {
            "role": "assistant",
            "content": "Let me search for the error.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {"name": "grep", "arguments": '{"pattern": "error"}'},
                }
            ],
        },
    ]
    blocks = segment_messages(messages, step=1)
    assert len(blocks) == 2
    assert blocks[0].block_type == BlockType.THOUGHT
    assert blocks[1].block_type == BlockType.ACTION
    assert "grep" in blocks[1].content


def test_segment_tool_result_file():
    messages = [
        {"role": "tool", "content": "def parse_input(data):\n    return data.split()", "name": "file_read"},
    ]
    blocks = segment_messages(messages, step=2)
    assert len(blocks) == 1
    assert blocks[0].block_type == BlockType.OBS_FILE


def test_segment_tool_result_error():
    messages = [
        {"role": "tool", "content": "Traceback (most recent call last):\n  File 'test.py'", "name": "bash"},
    ]
    blocks = segment_messages(messages, step=2)
    assert len(blocks) == 1
    assert blocks[0].block_type == BlockType.OBS_ERROR


def test_segment_tool_result_grep():
    messages = [
        {"role": "tool", "content": "src/main.py:42: def handle_error()", "name": "grep"},
    ]
    blocks = segment_messages(messages, step=2)
    assert len(blocks) == 1
    assert blocks[0].block_type == BlockType.OBS_GREP


def test_segment_new_step():
    blocks = segment_new_step(
        thought="I should read the file.",
        action={"function": {"name": "file_read", "arguments": '{"path": "foo.py"}'}},
        observation="def foo():\n    pass",
        step=3,
        start_block_id=5,
        tool_name="file_read",
    )
    assert len(blocks) == 3
    assert blocks[0].block_type == BlockType.THOUGHT
    assert blocks[1].block_type == BlockType.ACTION
    assert blocks[2].block_type == BlockType.OBS_FILE
    assert blocks[0].block_id == 5
    assert blocks[2].block_id == 7


def test_classify_observation_by_name():
    assert _classify_observation("anything", "grep") == BlockType.OBS_GREP
    assert _classify_observation("anything", "file_read") == BlockType.OBS_FILE
    assert _classify_observation("anything", "bash") == BlockType.OBS_SHELL


def test_classify_observation_by_content():
    assert _classify_observation("Traceback: error here") == BlockType.OBS_ERROR
    assert _classify_observation("src/foo.py:10: class Bar") == BlockType.OBS_GREP
    assert _classify_observation("def my_function():\n    return 42") == BlockType.OBS_FILE


def test_token_count_nonzero():
    blocks = segment_messages(
        [{"role": "system", "content": "Hello world"}],
        step=0,
    )
    assert blocks[0].token_count > 0
