"""Phase 7: drop+restore via fresh-mementoed → obs_dropped state machine.

The renderer should:
1. Render obs+markers when a memento is freshly written (so engine captures KV).
2. Render memento-only (no obs) once the runner has flipped `_obs_dropped=True`.
3. Render obs+markers again on recall (so vLLM's prefix cache hits the pinned blocks).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

_here = Path(__file__).resolve().parent
_repo_root = _here.parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

os.environ.setdefault("PAPER2_TEST_NO_VLLM", "1")

from studies.lifetime_cost.paper2.adapters.memento_vllm import (
    transform_messages, wrap_tool_message_for_masking, wrap_tool_message_inlined,
)
from studies.lifetime_cost.paper2.policy.memento_policy import MementoPolicy
from studies.lifetime_cost.pipeline.policies.base import CompactionContext
from studies.lifetime_cost.pipeline.tokenization import get_tokenizer


def _stub_writer():
    w = MagicMock()
    w.write.return_value = ("SUMMARY", MagicMock(input_tokens=10, output_tokens=5, cost_usd=0.001))
    return w


def test_renderer_drops_obs_when_obs_dropped_flag_set():
    big = "X" * 3000
    msgs = [
        {"role": "tool", "tool_call_id": "c1", "content": big,
         "memento": "[memento_id=mem-1]\nSUMMARY",
         "_obs_dropped": True},
        {"role": "tool", "tool_call_id": "c2", "content": big,
         "memento": "[memento_id=mem-2]\nSUMMARY"},  # no _obs_dropped
    ]
    out = transform_messages(msgs, last_only_masking=False)
    # First (drop=True): memento-only render, no obs in body.
    assert big not in out[0]["content"], "obs should be dropped from first msg"
    assert "[memento_id=mem-1]" in out[0]["content"]
    # Second (no drop flag): full obs + markers.
    assert big in out[1]["content"], "obs should be present in second msg"
    assert "<tool_response>" in out[1]["content"]


def test_renderer_keeps_obs_when_freshly_mementoed():
    """Even with last_only_masking=False, a freshly-mementoed msg renders
    obs+markers (so the engine captures). Only AFTER the capture chat does
    the runner flip _obs_dropped=True."""
    big = "X" * 3000
    msgs = [
        {"role": "tool", "tool_call_id": "c1", "content": big,
         "memento": "[memento_id=mem-1]\nSUMMARY",
         "_freshly_mementoed": True, "_obs_dropped": False},
    ]
    out = transform_messages(msgs, last_only_masking=False)
    assert big in out[0]["content"]
    assert "<tool_response>" in out[0]["content"]
    assert "<|fim_prefix|>" in out[0]["content"]


def test_drop_mode_compaction_marks_freshly_mementoed():
    tokenizer = get_tokenizer("tiktoken:cl100k_base")
    big = "X" * 4000

    msgs = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "tool_calls": [
            {"id": "c1", "function": {"name": "read", "arguments": {}}}]},
        {"role": "tool", "tool_call_id": "c1", "content": big},
        {"role": "assistant", "tool_calls": [
            {"id": "c2", "function": {"name": "read", "arguments": {}}}]},
        {"role": "tool", "tool_call_id": "c2", "content": big},
        {"role": "assistant", "tool_calls": [
            {"id": "c3", "function": {"name": "read", "arguments": {}}}]},
        {"role": "tool", "tool_call_id": "c3", "content": big},
    ]

    p = MementoPolicy(
        min_obs_chars=300, recall_mode="drop", recall_enabled=True,
        trigger_ratio=0.01, target_ratio=0.001,
        writer=_stub_writer(),
    )
    ctx = CompactionContext(step=0, budget=100, hard_budget=200,
                            tokenizer=tokenizer, summarizer=lambda x: ("", 0, 0, 0))
    out, evt = p.maybe_compact(msgs, ctx)
    assert evt is not None
    tagged = [m for m in out if m.get("role") == "tool" and m.get("memento")]
    assert len(tagged) == 1
    m = tagged[0]
    assert m.get("_freshly_mementoed") is True
    assert m.get("_obs_dropped") is False


def test_drop_mode_recall_clears_obs_dropped():
    p = MementoPolicy(
        min_obs_chars=300, recall_mode="drop",
        recall_enabled=True, recall_low_water_ratio=0.99,  # always trigger
        recall_strategy="lru",
        writer=_stub_writer(),
    )
    tokenizer = get_tokenizer("tiktoken:cl100k_base")
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "tool", "tool_call_id": "c1", "content": "X" * 4000,
         "memento": "[memento_id=mem-1]\nSUMMARY",
         "_freshly_mementoed": False, "_obs_dropped": True,
         "step_added": 0},
    ]
    ctx = CompactionContext(step=5, budget=10000, hard_budget=20000,
                            tokenizer=tokenizer, summarizer=lambda x: ("", 0, 0, 0))
    out, evt = p.maybe_recall(msgs, ctx)
    assert evt is not None, "recall should fire"
    target = next(m for m in out if m.get("role") == "tool")
    assert target.get("_obs_dropped") is False, \
        "recall should clear _obs_dropped so renderer puts obs back"


def test_full_lifecycle():
    """End-to-end: compaction marks fresh, runner-style flip drops obs,
    recall un-drops, renderer sequence renders correctly at each stage."""
    tokenizer = get_tokenizer("tiktoken:cl100k_base")
    big = "OBS_BYTES_" * 400

    msgs = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "tool_calls": [
            {"id": "c1", "function": {"name": "read", "arguments": {}}}]},
        {"role": "tool", "tool_call_id": "c1", "content": big},
        {"role": "assistant", "tool_calls": [
            {"id": "c2", "function": {"name": "read", "arguments": {}}}]},
        {"role": "tool", "tool_call_id": "c2", "content": big},
        {"role": "assistant", "tool_calls": [
            {"id": "c3", "function": {"name": "read", "arguments": {}}}]},
        {"role": "tool", "tool_call_id": "c3", "content": big},
    ]

    p = MementoPolicy(
        min_obs_chars=300, recall_mode="drop", recall_enabled=True,
        recall_strategy="lru", recall_low_water_ratio=0.99,
        trigger_ratio=0.01, target_ratio=0.001,
        writer=_stub_writer(),
    )
    ctx = CompactionContext(step=0, budget=10000, hard_budget=20000,
                            tokenizer=tokenizer, summarizer=lambda x: ("", 0, 0, 0))

    # Compaction round 1 — fresh memento, obs still present.
    msgs, _ = p.maybe_compact(msgs, ctx)
    fresh_msg = next(m for m in msgs if m.get("role") == "tool" and m.get("memento"))
    assert fresh_msg.get("_freshly_mementoed") is True

    # Render: should have obs+markers (capture chat).
    rendered = transform_messages(msgs, last_only_masking=False)
    fresh_idx = next(i for i, m in enumerate(msgs)
                     if m.get("role") == "tool" and m.get("memento"))
    assert big in rendered[fresh_idx]["content"]
    assert "<tool_response>" in rendered[fresh_idx]["content"]

    # Runner flips after chat.
    for m in msgs:
        if m.get("_freshly_mementoed"):
            m["_freshly_mementoed"] = False
            m["_obs_dropped"] = True

    # Render: should NOW be memento-only (drop chat).
    rendered = transform_messages(msgs, last_only_masking=False)
    assert big not in rendered[fresh_idx]["content"]
    assert "memento" in rendered[fresh_idx]["content"]

    # Recall: should clear _obs_dropped.
    ctx2 = CompactionContext(step=5, budget=10000, hard_budget=20000,
                             tokenizer=tokenizer, summarizer=lambda x: ("", 0, 0, 0))
    msgs, _ = p.maybe_recall(msgs, ctx2)

    # Render: should have obs+markers again (recall chat).
    rendered = transform_messages(msgs, last_only_masking=False)
    recalled_msg = next(m for m in msgs if m.get("recalled_step") is not None)
    recalled_idx = msgs.index(recalled_msg)
    assert big in rendered[recalled_idx]["content"], \
        "recall should bring obs back into the rendered prompt"


if __name__ == "__main__":
    test_renderer_drops_obs_when_obs_dropped_flag_set()
    test_renderer_keeps_obs_when_freshly_mementoed()
    test_drop_mode_compaction_marks_freshly_mementoed()
    test_drop_mode_recall_clears_obs_dropped()
    test_full_lifecycle()
    print("OK — all 5 phase 7 tests passed")
