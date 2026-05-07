"""Unit tests for the message-transform logic. No GPU/vLLM required."""

from studies.lifetime_cost.paper2.adapters.memento_vllm import (
    transform_messages,
    wrap_tool_message_for_masking,
    wrap_tool_message_inlined,
    SUMMARY_START_STR,
    SUMMARY_END_STR,
)


def test_wrap_no_memento():
    # No memento → plain text, no special tokens (avoids phantom-block cost).
    out = wrap_tool_message_for_masking("hello", None)
    assert out == "[tool_response]\nhello"
    assert "<tool_response>" not in out
    assert SUMMARY_START_STR not in out


def test_wrap_with_memento():
    out = wrap_tool_message_for_masking("file contents", "summary text")
    expected = (
        "<tool_response>\nfile contents\n</tool_response>"
        f"{SUMMARY_START_STR}summary text{SUMMARY_END_STR}"
    )
    assert out == expected


def test_transform_passes_non_tool_messages():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    out = transform_messages(msgs)
    assert out == msgs  # unchanged


def test_transform_tool_no_memento():
    msgs = [
        {"role": "user", "content": "fix"},
        {"role": "tool", "content": "obs", "tool_call_id": "a"},
    ]
    out = transform_messages(msgs)
    assert len(out) == 2
    assert out[0] == msgs[0]
    assert out[1]["role"] == "user"
    # Plain text — no <tool_response> tokens that would phantom-trigger the masker.
    assert out[1]["content"] == "[tool_response]\nobs"
    assert "<tool_response>" not in out[1]["content"]


def test_transform_tool_with_memento():
    msgs = [
        {"role": "tool", "content": "the obs", "memento": "summary"},
    ]
    out = transform_messages(msgs)
    assert out[0]["role"] == "user"
    body = out[0]["content"]
    assert body.startswith("<tool_response>\nthe obs\n</tool_response>")
    assert body.endswith(f"{SUMMARY_START_STR}summary{SUMMARY_END_STR}")


def test_summary_tokens_are_qwen3_reserved():
    # Sanity: these are the FIM tokens in Qwen3-4B vocab (151659/151660)
    assert SUMMARY_START_STR == "<|fim_prefix|>"
    assert SUMMARY_END_STR == "<|fim_middle|>"


def test_wrap_inlined_with_memento():
    out = wrap_tool_message_inlined("the obs", "the memento")
    # No marker tokens — this is what gets emitted for older turns under
    # last_only_masking=True.
    assert SUMMARY_START_STR not in out
    assert SUMMARY_END_STR not in out
    assert "<tool_response>" not in out
    assert "the memento" in out


def test_wrap_inlined_no_memento_falls_back_to_obs():
    out = wrap_tool_message_inlined("the obs", None)
    # No memento → plain text with no markers; we don't want to feed
    # phantom blocks to the masking processor.
    assert "<tool_response>" not in out
    assert SUMMARY_START_STR not in out
    assert "the obs" in out


def test_last_only_masking_marks_only_last_tool_message():
    msgs = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "calling"},
        {"role": "tool", "content": "obs1", "memento": "mem1"},
        {"role": "assistant", "content": "ok"},
        {"role": "tool", "content": "obs2", "memento": "mem2"},
        {"role": "assistant", "content": "done?"},
        {"role": "tool", "content": "obs3", "memento": "mem3"},
    ]
    out = transform_messages(msgs, last_only_masking=True)
    # 3 tool messages → 3 user messages in the same positions
    user_msgs = [m for m in out if m["role"] == "user"]
    # First user is the original "go", then 3 transformed tool→user
    assert len(user_msgs) == 4
    body1, body2, body3 = user_msgs[1]["content"], user_msgs[2]["content"], user_msgs[3]["content"]
    # Last (body3) has markers; earlier are inline plain text
    assert SUMMARY_START_STR in body3
    assert SUMMARY_END_STR in body3
    assert SUMMARY_START_STR not in body1
    assert SUMMARY_START_STR not in body2
    assert "mem1" in body1
    assert "mem2" in body2


def test_last_only_disabled_marks_all():
    msgs = [
        {"role": "tool", "content": "obs1", "memento": "mem1"},
        {"role": "tool", "content": "obs2", "memento": "mem2"},
    ]
    out = transform_messages(msgs, last_only_masking=False)
    bodies = [m["content"] for m in out]
    # Both have markers
    assert all(SUMMARY_START_STR in b and SUMMARY_END_STR in b for b in bodies)


if __name__ == "__main__":
    test_wrap_no_memento()
    test_wrap_with_memento()
    test_transform_passes_non_tool_messages()
    test_transform_tool_no_memento()
    test_transform_tool_with_memento()
    test_summary_tokens_are_qwen3_reserved()
    test_wrap_inlined_with_memento()
    test_wrap_inlined_no_memento_falls_back_to_obs()
    test_last_only_masking_marks_only_last_tool_message()
    test_last_only_disabled_marks_all()
    print("ALL UNIT TESTS PASSED")
