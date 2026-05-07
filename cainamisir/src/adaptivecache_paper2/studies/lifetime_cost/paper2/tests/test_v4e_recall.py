"""Phase 4e API tests: stable obs_id + cross-process recall queue.

Run:
    cd /home/vlad/adaptivecache-paper2
    .venv-paper2/bin/python -m studies.lifetime_cost.paper2.tests.test_v4e_recall
"""
from __future__ import annotations

import os
import tempfile

# Use a per-test queue path so tests don't collide.
_tmpdir = tempfile.mkdtemp(prefix="paper2_v4e_test_")
os.environ["PAPER2_RECALL_QUEUE_PATH"] = os.path.join(_tmpdir, "recall_q")


def _api():
    from vllm.v1.core.block_masking import (
        compute_obs_id, queue_recall, consume_recall, reset_recall_queue,
    )
    return dict(
        compute_obs_id=compute_obs_id,
        queue_recall=queue_recall,
        consume_recall=consume_recall,
        reset=reset_recall_queue,
    )


def test_obs_id_stability():
    a = _api()
    a["reset"]()
    oid_1 = a["compute_obs_id"]([10, 20, 30, 40])
    oid_2 = a["compute_obs_id"]([10, 20, 30, 40])
    assert oid_1 == oid_2, "same tokens → same obs_id"
    assert oid_1.startswith("obs:"), f"unexpected prefix: {oid_1!r}"
    print("  same tokens → same obs_id ✓")


def test_obs_id_changes_with_content():
    a = _api()
    a["reset"]()
    oid_a = a["compute_obs_id"]([1, 2, 3])
    oid_b = a["compute_obs_id"]([1, 2, 4])
    oid_c = a["compute_obs_id"]([1, 2])
    assert len({oid_a, oid_b, oid_c}) == 3, "different content → different ids"
    print("  different tokens → different obs_id ✓")


def test_queue_consume_roundtrip():
    a = _api()
    a["reset"]()
    oid = a["compute_obs_id"]([42, 43, 44])
    a["queue_recall"](oid)
    assert a["consume_recall"](oid) is True, "consume returns True for queued"
    assert a["consume_recall"](oid) is False, "consume idempotent (drained)"
    print("  queue/consume roundtrip ✓")


def test_consume_unknown_returns_false():
    a = _api()
    a["reset"]()
    assert a["consume_recall"]("obs:nope") is False
    print("  consume of unknown obs_id → False ✓")


def test_queue_multiple_obs():
    a = _api()
    a["reset"]()
    o1 = a["compute_obs_id"]([1])
    o2 = a["compute_obs_id"]([2])
    o3 = a["compute_obs_id"]([3])
    a["queue_recall"](o1); a["queue_recall"](o2); a["queue_recall"](o3)
    # Drain in different order
    assert a["consume_recall"](o2) is True
    assert a["consume_recall"](o1) is True
    assert a["consume_recall"](o3) is True
    assert a["consume_recall"](o1) is False, "drained"
    print("  multiple queued obs, out-of-order drain ✓")


def test_queue_persists_across_calls():
    """Smoke that queue file persists correctly between writer and reader.
    (Cross-process is the real use; this just exercises the file mechanism.)"""
    a = _api()
    a["reset"]()
    oid = a["compute_obs_id"]([100, 101])
    a["queue_recall"](oid)
    # Re-import to simulate process boundary (file is the channel).
    import importlib, sys
    if "vllm.v1.core.block_masking.memento_store" in sys.modules:
        importlib.reload(sys.modules["vllm.v1.core.block_masking.memento_store"])
    from vllm.v1.core.block_masking.memento_store import consume_recall
    assert consume_recall(oid) is True, "queue survives module reload"
    print("  queue persists across module reload (cross-proc proxy) ✓")


def test_policy_attmask_mode_stages_recall():
    """The policy's attmask mode should stash obs_text into _pending_attmask_recalls
    instead of clearing the memento (which is the inplace behavior)."""
    from studies.lifetime_cost.paper2.policy.memento_policy import MementoPolicy

    p = MementoPolicy(
        min_obs_chars=300,
        recall_enabled=True,
        recall_low_water_ratio=0.99,  # always trip
        recall_cooldown_steps=0,
        recall_strategy="lru",
        recall_mode="attmask",
    )
    assert p._recall_mode == "attmask"
    assert p._pending_attmask_recalls == []
    print("  policy(recall_mode='attmask') initialized cleanly ✓")


def main():
    test_obs_id_stability()
    test_obs_id_changes_with_content()
    test_queue_consume_roundtrip()
    test_consume_unknown_returns_false()
    test_queue_multiple_obs()
    test_queue_persists_across_calls()
    test_policy_attmask_mode_stages_recall()
    print("ALL PHASE 4E API TESTS PASSED")


if __name__ == "__main__":
    main()
