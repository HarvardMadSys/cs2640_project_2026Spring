from studies.lifetime_cost.pipeline.policies import build_policy
from studies.lifetime_cost.pipeline.policies.base import CompactionContext
from studies.lifetime_cost.pipeline.tokenization import get_tokenizer


def _ctx(step=0, budget=2000, hard=4000):
    tok = get_tokenizer("tiktoken:cl100k_base")
    return CompactionContext(
        step=step,
        budget=budget,
        hard_budget=hard,
        tokenizer=tok,
        # Summarizer returns (text, in_cached, in_uncached, out_tokens).
        # Cached tokens are the messages-to-summarize themselves; they hit the
        # provider's prefix cache from the just-completed agent step.
        summarizer=lambda msgs: ("[SUM]", 80, 50, 8),
    )


def _msgs(n_turns: int, msg_chars: int = 800):
    out = [
        {"role": "system", "content": "system instructions go here"},
        {"role": "user", "content": "do the task please"},
    ]
    for i in range(n_turns):
        out.append({"role": "assistant", "content": f"thinking step {i}"})
        out.append({"role": "tool", "content": "x" * msg_chars, "tool_call_id": str(i)})
    return out


def test_no_compaction_is_identity():
    pol = build_policy("none")
    msgs = _msgs(20)
    out, evt = pol.maybe_compact(msgs, _ctx())
    assert out == msgs
    assert evt is None


def test_naive_summary_fires_when_over_budget():
    pol = build_policy("naive_summary", recent_keep=2, trigger_ratio=0.5)
    msgs = _msgs(40, msg_chars=500)
    out, evt = pol.maybe_compact(msgs, _ctx(budget=2000))
    assert evt is not None
    assert len(out) < len(msgs)


def test_naive_summary_skipped_when_under_budget():
    pol = build_policy("naive_summary")
    msgs = _msgs(2, msg_chars=50)
    out, evt = pol.maybe_compact(msgs, _ctx(budget=64_000))
    assert evt is None
    assert out == msgs


def test_microcompact_only_rewrites_oversized_msgs():
    pol = build_policy("microcompact", per_msg_threshold_tokens=100, trigger_ratio=0.5)
    msgs = _msgs(30, msg_chars=2000)
    out, evt = pol.maybe_compact(msgs, _ctx(budget=2000))
    assert evt is not None
    # System and user task should remain identical
    assert out[0] == msgs[0]
    assert out[1] == msgs[1]


def test_prefix_preserving_keeps_head_byte_identical():
    pol = build_policy("prefix_preserving", keep_first_turns=4, keep_recent_turns=2,
                       trigger_ratio=0.5)
    msgs = _msgs(20, msg_chars=500)
    out, evt = pol.maybe_compact(msgs, _ctx(budget=2000))
    assert evt is not None
    # The system + first user should be byte-identical
    assert out[0] == msgs[0]
    assert out[1] == msgs[1]


def test_prefix_preserving_frozen_prefix_is_stable_across_compactions():
    pol = build_policy("prefix_preserving", keep_first_turns=3, keep_recent_turns=2,
                       trigger_ratio=0.5)
    msgs = _msgs(20, msg_chars=500)
    out1, _ = pol.maybe_compact(msgs, _ctx(budget=2000, step=5))
    head1 = out1[: pol._frozen_idx]

    msgs2 = list(out1) + [{"role": "assistant", "content": "more"}, {"role": "tool", "content": "x" * 500, "tool_call_id": "later"}] * 5
    out2, _ = pol.maybe_compact(msgs2, _ctx(budget=2000, step=10))
    head2 = out2[: pol._frozen_idx]
    assert head1 == head2  # frozen prefix invariant


def test_boundary_aware_defers_when_no_boundary():
    pol = build_policy("boundary_aware", keep_first_turns=4, keep_recent_turns=2,
                       trigger_ratio=0.5, boundary_grace_steps=1)
    msgs = _msgs(20, msg_chars=500)
    # No boundary keyword in the assistant messages → soft full but no compaction
    out, evt = pol.maybe_compact(msgs, _ctx(budget=2000, hard=64_000))
    assert evt is None


def test_boundary_aware_fires_at_hard_budget():
    pol = build_policy("boundary_aware", keep_first_turns=4, keep_recent_turns=2,
                       trigger_ratio=0.5)
    msgs = _msgs(40, msg_chars=2000)  # very large
    out, evt = pol.maybe_compact(msgs, _ctx(budget=2000, hard=4000))
    assert evt is not None    # hard budget overrides boundary requirement
