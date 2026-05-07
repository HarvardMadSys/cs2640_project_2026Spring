"""Unit tests for MementoPolicy recall (v1). No GPU/network required.

Verifies:
- maybe_recall is a noop when no mementos are present
- maybe_recall is a noop when the prompt is over the low-water mark
- LRU pick: recall targets the most-recently-mementoed tool message (highest index)
- recall clears `memento`, stashes `prior_memento`, and stamps `recalled_step`
- maybe_compact honors `recalled_step` cooldown — won't immediately re-memento
- After cooldown, maybe_compact reuses `prior_memento` instead of calling the writer
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from studies.lifetime_cost.pipeline.policies.base import CompactionContext
from studies.lifetime_cost.paper2.policy.memento_policy import MementoPolicy


class _StubTokenizer:
    """Char-count tokenizer — predictable totals for budget-driven tests."""

    def encode(self, text: str) -> List[int]:
        return [0] * len(text)

    def count(self, text: str) -> int:
        return len(text)


@dataclass
class _FakeUsage:
    input_tokens: int = 1
    output_tokens: int = 1
    cost_usd: float = 0.0


class _FakeWriter:
    """Counts calls so tests can assert when Haiku would have been hit."""

    def __init__(self):
        self.calls = 0

    def write(self, obs: str, *, tool_name: str = "", tool_args=None) -> Tuple[str, _FakeUsage]:
        self.calls += 1
        return f"MEMENTO[{tool_name}][{len(obs)}c]", _FakeUsage()


def _ctx(step: int, budget: int = 10_000) -> CompactionContext:
    return CompactionContext(
        step=step,
        budget=budget,
        hard_budget=budget * 2,
        tokenizer=_StubTokenizer(),
    )


def _msgs_with_mementos(*, mement_indices=(2,), big_obs_chars=100) -> List[Dict]:
    """Build a small conversation with tool obs at fixed positions.

    Layout: [user, assistant(tc1), tool(0), assistant(tc2), tool(1), assistant(tc3), tool(2)]
    Indices of tool messages: 2, 4, 6.
    """
    obs = "X" * big_obs_chars
    msgs: List[Dict] = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t0", "function": {"name": "f", "arguments": {}}}]},
        {"role": "tool", "tool_call_id": "t0", "content": obs + "_0"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "function": {"name": "f", "arguments": {}}}]},
        {"role": "tool", "tool_call_id": "t1", "content": obs + "_1"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t2", "function": {"name": "f", "arguments": {}}}]},
        {"role": "tool", "tool_call_id": "t2", "content": obs + "_2"},
    ]
    tool_positions = [i for i, m in enumerate(msgs) if m.get("role") == "tool"]
    for k in mement_indices:
        msgs[tool_positions[k]]["memento"] = f"prior_mem_{k}"
    return msgs


def _make_policy(**overrides) -> Tuple[MementoPolicy, _FakeWriter]:
    writer = _FakeWriter()
    defaults = dict(
        min_obs_chars=10,
        trigger_ratio=0.85,
        target_ratio=0.55,
        recall_low_water_ratio=0.60,
        recall_cooldown_steps=3,
        writer=writer,
    )
    defaults.update(overrides)
    return MementoPolicy(**defaults), writer


def test_recall_noop_when_no_mementos():
    policy, _ = _make_policy()
    msgs = _msgs_with_mementos(mement_indices=())  # nothing mementoed
    out, evt = policy.maybe_recall(msgs, _ctx(step=5))
    assert evt is None
    assert out is msgs  # not modified


def test_recall_noop_when_above_low_water():
    # Budget 10K, low_water 6K. The rendered estimator counts mementoed obs
    # as just the memento text (short), not the underlying content. So to
    # trip "above low water" we need a big un-mementoed obs.
    policy, _ = _make_policy()
    msgs = _msgs_with_mementos(mement_indices=(0,), big_obs_chars=50)
    # Add a big unmementoed user message to push rendered total over 6K.
    msgs.append({"role": "user", "content": "Z" * 7000})
    out, evt = policy.maybe_recall(msgs, _ctx(step=5))
    assert evt is None
    assert out is msgs


def test_recall_lru_picks_highest_index():
    # Three mementoed tool messages. LRU should target the highest-index one.
    policy, _ = _make_policy()
    msgs = _msgs_with_mementos(mement_indices=(0, 1, 2), big_obs_chars=50)
    out, evt = policy.maybe_recall(msgs, _ctx(step=5))
    assert evt is not None
    assert ".recall." in evt.policy  # "memento.recall.inplace" or ".append"
    # Tool position 2 is index 6 in the message list
    assert out[6].get("memento") is None
    assert out[6].get("prior_memento") == "prior_mem_2"
    assert out[6].get("recalled_step") == 5
    # Earlier mementos untouched
    assert out[2].get("memento") == "prior_mem_0"
    assert out[4].get("memento") == "prior_mem_1"


def test_recall_then_compact_respects_cooldown():
    # Recall at step 5 → maybe_compact at step 6 must SKIP that message.
    policy, writer = _make_policy(recall_cooldown_steps=3, trigger_ratio=0.0)
    # trigger_ratio=0 forces compact to always try; we need that to test the
    # message-level skip. With min_obs_chars=10 the obs qualify.
    msgs = _msgs_with_mementos(mement_indices=(0, 2), big_obs_chars=100)
    # Recall step 5 — clears highest mementoed (position 2, msg index 6)
    msgs, _ = policy.maybe_recall(msgs, _ctx(step=5))
    assert msgs[6].get("recalled_step") == 5
    # Compact step 6 — cooldown still active (6 - 5 = 1 < 3)
    msgs, evt = policy.maybe_compact(msgs, _ctx(step=6))
    # The recalled message must NOT have been re-mementoed
    assert msgs[6].get("memento") is None
    # `recent_skip=2` means tool positions 1 and 2 (msg indices 4 and 6) are
    # skipped from candidates, so only position 0 (msg index 2) was eligible.
    # That one already had a memento, so nothing fired.
    if evt is not None:
        # If something fired, it can't be msg index 6
        assert msgs[6].get("memento") is None


def test_recall_then_compact_reuses_stash_after_cooldown():
    # Recall, wait out the cooldown, compact — should reuse stashed memento
    # without calling the writer.
    policy, writer = _make_policy(
        recall_cooldown_steps=2,
        trigger_ratio=0.0,  # force compact path
        target_ratio=0.0,
        min_obs_chars=10,
    )
    # Use 5 tool messages — recent_skip=2 reserves the last 2 from
    # eviction; we'll test reuse on position 2 (which is older than the
    # protected window).
    msgs: List[Dict] = [{"role": "user", "content": "go"}]
    obs_text = "X" * 100
    for i in range(5):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"t{i}", "function": {"name": "f", "arguments": {}}}]})
        msgs.append({"role": "tool", "tool_call_id": f"t{i}", "content": obs_text + f"_{i}"})
    # Pre-memento the *first* tool obs (oldest) so recall picks it.
    # Actually, recall picks LRU = highest-index mementoed. We want to
    # test: pre-memento all 5, recall hits the most recent of them, then
    # cooldown expires, compact restores. To make the target eligible for
    # compact, it must NOT be in the recent_skip=2 window. Pre-memento
    # only positions 0..2 (the older ones); recall hits position 2 (msg
    # index 6); recent_skip excludes positions 3-4 (indices 8 and 10).
    tool_positions = [i for i, m in enumerate(msgs) if m.get("role") == "tool"]
    for k in (0, 1, 2):
        msgs[tool_positions[k]]["memento"] = f"old_mem_{k}"
    # Recall at step 5 — should target position 2 (msg index = tool_positions[2])
    msgs, recall_evt = policy.maybe_recall(msgs, _ctx(step=5))
    assert recall_evt is not None
    target_idx = tool_positions[2]
    assert msgs[target_idx]["memento"] is None
    assert msgs[target_idx]["prior_memento"] == "old_mem_2"
    # Compact at step 5 + cooldown (=7) — eligible again
    pre_calls = writer.calls
    msgs, comp_evt = policy.maybe_compact(msgs, _ctx(step=7))
    assert comp_evt is not None
    # The stashed text was reused, no new writer call for that message
    assert msgs[target_idx]["memento"] == "old_mem_2"
    assert msgs[target_idx].get("prior_memento") is None
    # writer.calls may have grown for OTHER tool messages, but not for the stashed one
    # (the stashed reuse is free). Verify: at most 2 new calls (positions 0 and 1
    # were already mementoed so they're skipped; position 2 is the stash; positions
    # 3-4 are in recent_skip). So calls == pre_calls.
    assert writer.calls == pre_calls


def test_embedding_strategy_picks_by_similarity_without_loading_model():
    """Stub the EmbeddingRecall._embed method so no MiniLM download is needed.

    The stub returns a unit vector keyed off a substring presence — this
    lets us assert that the strategy chooses the candidate whose memento
    text is more 'similar' to the query, even though both candidates are
    valid LRU targets.
    """
    import numpy as np
    from studies.lifetime_cost.paper2.policy.recall_strategy import EmbeddingRecall

    # Mark mementos at tool positions 0 and 1 (msg indices 2 and 4).
    msgs = _msgs_with_mementos(mement_indices=(0, 1), big_obs_chars=50)
    # Customize memento text so similarity differs.
    msgs[2]["memento"] = "the requests/models.py module"
    msgs[4]["memento"] = "totally unrelated CHANGELOG entry"

    strat = EmbeddingRecall(threshold=0.0)  # accept anything

    def fake_embed(text):
        # Project onto a 2D plane: axis 0 = "models" content, axis 1 = "changelog"
        v = np.array([
            1.0 if "models" in text else 0.0,
            1.0 if "CHANGELOG" in text or "changelog" in text else 0.0,
        ], dtype=np.float32)
        n = float(np.linalg.norm(v) + 1e-9)
        return v / n

    strat._embed = fake_embed  # bypass model load

    pick_idx = strat.pick(
        msgs, step=5, recent_text="What's in requests/models.py?"
    )
    # Should pick the models.py-themed memento (msg index 2), not CHANGELOG (msg index 4).
    # Even though LRU would have picked 4 (highest index).
    assert pick_idx == 2, f"expected 2 (models.py memento), got {pick_idx}"


def test_recall_append_mode_preserves_original_and_adds_addendum():
    """Append mode: original message keeps its memento; a synthetic user
    message is added at the tail with the full obs. The chronological
    prefix is unchanged so the prefix cache survives."""
    policy, _ = _make_policy(recall_mode="append")
    msgs = _msgs_with_mementos(mement_indices=(0, 1, 2), big_obs_chars=50)
    n_before = len(msgs)
    msgs_in = list(msgs)  # snapshot to verify input not mutated in place

    out, evt = policy.maybe_recall(msgs, _ctx(step=5))

    # Event reflects the mode in the policy name.
    assert evt is not None
    assert evt.policy.endswith(".append")

    # Output is a NEW list with one extra message at the tail.
    assert len(out) == n_before + 1

    # Original mementoed messages are untouched (no fall-back to full obs).
    assert out[2].get("memento") == "prior_mem_0"
    assert out[4].get("memento") == "prior_mem_1"
    assert out[6].get("memento") == "prior_mem_2"
    # Recalled-step bookkeeping still set on the targeted message.
    assert out[6].get("recalled_step") == 5

    # Addendum at tail: user role, full obs visible, marker fields set.
    add = out[-1]
    assert add["role"] == "user"
    assert add.get("_recall_marker") is True
    assert add.get("_recall_target_idx") == 6
    # The original obs (X*50 + "_2") must appear verbatim in the addendum.
    assert "X" * 50 in add["content"]
    assert "_2" in add["content"]
    assert "[recalled," in add["content"]


def test_recall_append_mode_does_not_block_compaction_of_target():
    """In append mode, the original mementoed message stays mementoed and the
    addendum is a user-role message (which compaction never touches). So
    nothing about cooldown/prior-memento logic should be exercised — the
    target stays compacted and no Haiku re-call is ever needed."""
    policy, writer = _make_policy(
        recall_mode="append",
        recall_cooldown_steps=2,
        trigger_ratio=0.0,
        target_ratio=0.0,
        min_obs_chars=10,
    )
    msgs = _msgs_with_mementos(mement_indices=(0, 1, 2), big_obs_chars=50)
    msgs, recall_evt = policy.maybe_recall(msgs, _ctx(step=5))
    assert recall_evt is not None
    target_idx = 6
    # Target stays mementoed in append mode → no need for prior_memento stash.
    assert msgs[target_idx].get("memento") == "prior_mem_2"
    assert msgs[target_idx].get("prior_memento") is None
    # Compact at later step — nothing should re-memento the target (already mementoed).
    pre_calls = writer.calls
    msgs, _ = policy.maybe_compact(msgs, _ctx(step=10))
    assert msgs[target_idx].get("memento") == "prior_mem_2"
    assert writer.calls == pre_calls  # no Haiku for the addendum (it's user role)


def test_embedding_strategy_below_threshold_returns_none():
    """When best similarity < threshold, the strategy should decline to recall."""
    import numpy as np
    from studies.lifetime_cost.paper2.policy.recall_strategy import EmbeddingRecall

    msgs = _msgs_with_mementos(mement_indices=(0,), big_obs_chars=50)
    msgs[2]["memento"] = "module XYZ"

    strat = EmbeddingRecall(threshold=0.5)

    def fake_embed(text):
        # Both vectors orthogonal — cosine = 0
        if "XYZ" in text:
            return np.array([1.0, 0.0], dtype=np.float32)
        return np.array([0.0, 1.0], dtype=np.float32)

    strat._embed = fake_embed
    pick_idx = strat.pick(msgs, step=5, recent_text="something else entirely")
    assert pick_idx is None


if __name__ == "__main__":
    test_recall_noop_when_no_mementos()
    test_recall_noop_when_above_low_water()
    test_recall_lru_picks_highest_index()
    test_recall_then_compact_respects_cooldown()
    test_recall_then_compact_reuses_stash_after_cooldown()
    test_recall_append_mode_preserves_original_and_adds_addendum()
    test_recall_append_mode_does_not_block_compaction_of_target()
    test_embedding_strategy_picks_by_similarity_without_loading_model()
    test_embedding_strategy_below_threshold_returns_none()
    print("ALL RECALL UNIT TESTS PASSED")
