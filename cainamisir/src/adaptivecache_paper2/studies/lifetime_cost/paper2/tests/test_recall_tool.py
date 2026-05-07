"""Unit tests for Phase 5: model-controlled recall tool.

Verifies:
* Policy tags mementos with [memento_id=mem-N] when recall_tool_enabled.
* `_recall_table` has the obs text under that id.
* `get_recall_tool` returns a Tool whose handler calls queue_recall_fn
  with the looked-up obs text.
* Errors gracefully on unknown ids.
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

# Avoid importing the vllm-heavy adapter chain.
os.environ.setdefault("PAPER2_TEST_NO_VLLM", "1")

from studies.lifetime_cost.paper2.policy.memento_policy import MementoPolicy


def _stub_writer():
    w = MagicMock()
    w.write.return_value = ("SUMMARY", MagicMock(input_tokens=10, output_tokens=5, cost_usd=0.001))
    return w


def test_disabled_policy_does_not_tag():
    p = MementoPolicy(recall_tool_enabled=False, writer=_stub_writer())
    assert p.get_recall_tool(lambda s: None) is None
    assert p.get_system_prompt_addendum() is None
    assert p._recall_table == {}


def test_enabled_policy_returns_tool_and_addendum():
    p = MementoPolicy(recall_tool_enabled=True, writer=_stub_writer())
    queue_recall_fn = MagicMock(return_value="obs:fakehash")
    tool = p.get_recall_tool(queue_recall_fn)
    assert tool is not None
    assert tool.name == "recall"
    assert "memento_id" in tool.parameters["properties"]
    assert tool.parameters["required"] == ["memento_id"]
    assert "memento_id" in (p.get_system_prompt_addendum() or "")


def test_recall_handler_round_trip():
    p = MementoPolicy(recall_tool_enabled=True, writer=_stub_writer())
    # Simulate the policy having tagged a memento during compaction.
    p._recall_table["mem-3"] = "FULL OBS TEXT"
    queue_recall_fn = MagicMock(return_value="obs:abc")
    tool = p.get_recall_tool(queue_recall_fn)

    res = tool.fn({"memento_id": "mem-3"})
    queue_recall_fn.assert_called_once_with("FULL OBS TEXT")
    assert "OK" in res
    assert "mem-3" in res


def test_recall_handler_unknown_id():
    p = MementoPolicy(recall_tool_enabled=True, writer=_stub_writer())
    p._recall_table["mem-1"] = "X"
    queue_recall_fn = MagicMock()
    tool = p.get_recall_tool(queue_recall_fn)

    res = tool.fn({"memento_id": "mem-99"})
    queue_recall_fn.assert_not_called()
    assert "[recall error]" in res
    assert "mem-1" in res  # known list shown


def test_recall_handler_missing_id():
    p = MementoPolicy(recall_tool_enabled=True, writer=_stub_writer())
    tool = p.get_recall_tool(lambda s: None)
    res = tool.fn({})
    assert "[recall error]" in res


def test_compaction_tags_memento():
    """maybe_compact should prefix [memento_id=mem-N] and register obs in table."""
    from studies.lifetime_cost.pipeline.policies.base import CompactionContext
    from studies.lifetime_cost.pipeline.tokenization import get_tokenizer

    tokenizer = get_tokenizer("tiktoken:cl100k_base")
    big_obs = "X" * 4000  # 4KB, comfortably above min_obs_chars=500
    asst_thinking = "Long reasoning text " * 50  # ~1KB asst content

    # Build messages: system, user, asst_with_tool_call, tool_obs (big), ...
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": asst_thinking, "tool_calls": [
            {"id": "c1", "function": {"name": "read", "arguments": {"path": "foo.py"}}}
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": big_obs},
        {"role": "assistant", "content": asst_thinking, "tool_calls": [
            {"id": "c2", "function": {"name": "read", "arguments": {"path": "bar.py"}}}
        ]},
        {"role": "tool", "tool_call_id": "c2", "content": big_obs},
        {"role": "assistant", "content": asst_thinking, "tool_calls": [
            {"id": "c3", "function": {"name": "read", "arguments": {"path": "baz.py"}}}
        ]},
        {"role": "tool", "tool_call_id": "c3", "content": big_obs},
        {"role": "user", "content": "u2"},
    ]

    p = MementoPolicy(
        min_obs_chars=300,
        recall_tool_enabled=True,
        # Force trigger: tiny budget so estimated tokens > trigger.
        trigger_ratio=0.01,
        target_ratio=0.001,  # Force fire as much as policy allows
        writer=_stub_writer(),
    )
    ctx = CompactionContext(step=0, budget=100, hard_budget=200,
                            tokenizer=tokenizer, summarizer=lambda x: ("", 0, 0, 0))
    out, evt = p.maybe_compact(msgs, ctx)
    assert evt is not None, "compaction should have fired"

    # Phase 6 cap: only ONE memento per call (not the burst of 3 the prior
    # design would fire under target_ratio=0.001).
    tool_msgs = [m for m in out if m.get("role") == "tool"]
    tagged = [m for m in tool_msgs if m.get("memento")]
    assert len(tagged) == 1, f"phase 6 cap: expect exactly 1 tagged, got {len(tagged)}"
    m = tagged[0]
    assert m["memento"].startswith("[memento_id=mem-"), m["memento"][:50]
    assert "memento_id" in m
    assert p._recall_table[m["memento_id"]] == big_obs

    # Phase 6: the asst that issued this tool_call should also be compacted —
    # bulky reasoning collapsed to a brief ref.
    target_id = m["tool_call_id"]
    paired_asst = next(
        m2 for m2 in out
        if m2.get("role") == "assistant"
        and any(tc.get("id") == target_id for tc in (m2.get("tool_calls") or []))
    )
    assert paired_asst.get("_step_compacted") is True
    assert "[step compacted:" in (paired_asst.get("content") or "")
    assert len(paired_asst["content"]) < 200, "asst content should be brief now"
    # Other asst turns should NOT be touched.
    untouched = [m2 for m2 in out
                 if m2.get("role") == "assistant" and not m2.get("_step_compacted")]
    for m2 in untouched:
        assert m2.get("content") == asst_thinking, "untouched asst should keep its content"


def test_phase6_one_per_call():
    """Phase 6: even with many candidates, only one memento fires per call."""
    from studies.lifetime_cost.pipeline.policies.base import CompactionContext
    from studies.lifetime_cost.pipeline.tokenization import get_tokenizer

    tokenizer = get_tokenizer("tiktoken:cl100k_base")
    big = "X" * 4000

    msgs = [{"role": "system", "content": "s"}]
    for k in range(8):
        msgs.append({"role": "assistant", "content": "thinking",
                     "tool_calls": [{"id": f"c{k}",
                                     "function": {"name": "read", "arguments": {}}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{k}", "content": big})

    p = MementoPolicy(
        min_obs_chars=300, recall_tool_enabled=False,
        trigger_ratio=0.01, target_ratio=0.001,
        writer=_stub_writer(),
    )
    ctx = CompactionContext(step=0, budget=100, hard_budget=200,
                            tokenizer=tokenizer, summarizer=lambda x: ("", 0, 0, 0))
    out, evt = p.maybe_compact(msgs, ctx)
    tagged = [m for m in out if m.get("role") == "tool" and m.get("memento")]
    assert len(tagged) == 1, f"phase 6 cap: expect 1, got {len(tagged)}"


if __name__ == "__main__":
    test_disabled_policy_does_not_tag()
    test_enabled_policy_returns_tool_and_addendum()
    test_recall_handler_round_trip()
    test_recall_handler_unknown_id()
    test_recall_handler_missing_id()
    test_compaction_tags_memento()
    test_phase6_one_per_call()
    print("OK — all 7 tests passed")
