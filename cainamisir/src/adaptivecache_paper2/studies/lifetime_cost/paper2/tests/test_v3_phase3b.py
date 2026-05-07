"""Phase 3b API smoke: queue_kv_restore allocates + queues the op.

Mocks the scheduler's `kv_cache_manager.block_pool` so we can exercise
the API path without booting a real vLLM engine. Proves:

* queue_kv_restore unknown memento_id → returns False
* queue_kv_restore with memento metadata + free blocks → allocates
  N blocks, returns True with target_block_ids in the message
* The op lands in `pending_kv_restore_operations[request_id]`
* The op carries the memento_id + the allocated block IDs

The end-to-end engine test (capture → queue_kv_restore via scheduler →
worker restore) lives in Phase 3c smoke.

Run:
    cd /home/vlad/adaptivecache-paper2
    .venv-paper2/bin/python -m studies.lifetime_cost.paper2.tests.test_v3_phase3b
"""
from __future__ import annotations


def _api():
    from vllm.v1.core.block_masking import (
        StoredMemento,
        KVRestoreOp,
        global_memento_store,
        reset_global_memento_store,
    )
    from vllm.v1.core.sched.scheduler import Scheduler
    return {
        "StoredMemento": StoredMemento,
        "KVRestoreOp": KVRestoreOp,
        "global_memento_store": global_memento_store,
        "reset": reset_global_memento_store,
        "Scheduler": Scheduler,
    }


class _MockBlockPool:
    """Returns N fresh KVCacheBlock-shaped stand-ins with sequential IDs."""

    def __init__(self, free_blocks: int = 100):
        self._free = free_blocks
        self._next_id = 1000

    def get_num_free_blocks(self) -> int:
        return self._free

    def get_new_blocks(self, num_blocks: int):
        if num_blocks > self._free:
            raise ValueError(f"only {self._free} free")
        self._free -= num_blocks
        from types import SimpleNamespace
        out = []
        for _ in range(num_blocks):
            out.append(SimpleNamespace(block_id=self._next_id, ref_cnt=1))
            self._next_id += 1
        return out


class _MockKVCacheManager:
    def __init__(self, free_blocks: int = 100):
        self.block_pool = _MockBlockPool(free_blocks=free_blocks)


class _MockScheduler:
    """Bare-minimum harness that re-uses Scheduler.queue_kv_restore.

    queue_kv_restore only touches:
      - self.kv_cache_manager.block_pool.get_new_blocks
      - self.pending_kv_restore_operations
    so we can stub the rest.
    """

    def __init__(self, free_blocks: int = 100):
        self.kv_cache_manager = _MockKVCacheManager(free_blocks=free_blocks)
        self.pending_kv_restore_operations: dict[str, list] = {}


def test_queue_kv_restore_unknown_memento_returns_false():
    api = _api()
    api["reset"]()
    s = _MockScheduler()
    s.queue_kv_restore = api["Scheduler"].queue_kv_restore.__get__(s)
    ok, msg = s.queue_kv_restore(request_id="r1", memento_id="nope")
    assert ok is False
    assert "unknown" in msg
    assert s.pending_kv_restore_operations == {}
    api["reset"]()
    print("  unknown memento_id → False ✓")


def test_queue_kv_restore_allocates_and_queues():
    api = _api()
    api["reset"]()
    sched_store = api["global_memento_store"]()
    sched_store.stash(api["StoredMemento"](
        memento_id="m_test_42",
        request_id="orig_r0",
        logical_range=(64, 128),
        physical_block_ids=[10, 11, 12, 13],   # 4-block memento
        block_size=16,
        num_layers=0,
        cpu_kv=None,
    ))

    s = _MockScheduler(free_blocks=10)
    s.queue_kv_restore = api["Scheduler"].queue_kv_restore.__get__(s)
    ok, msg = s.queue_kv_restore(request_id="new_r1", memento_id="m_test_42")
    assert ok is True, msg
    assert "queued restore: 4 blocks" in msg

    # Op landed in the pending dict.
    assert "new_r1" in s.pending_kv_restore_operations
    ops = s.pending_kv_restore_operations["new_r1"]
    assert len(ops) == 1
    op = ops[0]
    assert isinstance(op, api["KVRestoreOp"])
    assert op.memento_id == "m_test_42"
    assert op.request_id == "new_r1"
    assert len(op.target_block_ids) == 4
    # Allocator gave sequential IDs starting at 1000
    assert op.target_block_ids == [1000, 1001, 1002, 1003]
    api["reset"]()
    print("  4-block memento → allocated [1000..1003], queued correctly ✓")


def test_queue_kv_restore_oom_returns_false():
    api = _api()
    api["reset"]()
    sched_store = api["global_memento_store"]()
    sched_store.stash(api["StoredMemento"](
        memento_id="m_big",
        request_id="orig",
        logical_range=(0, 1600),
        physical_block_ids=list(range(100)),   # 100-block memento
        block_size=16,
        num_layers=0,
        cpu_kv=None,
    ))
    s = _MockScheduler(free_blocks=5)  # only 5 free → can't fit 100
    s.queue_kv_restore = api["Scheduler"].queue_kv_restore.__get__(s)
    ok, msg = s.queue_kv_restore(request_id="r", memento_id="m_big")
    assert ok is False
    assert "block allocation failed" in msg
    assert s.pending_kv_restore_operations == {}
    api["reset"]()
    print("  OOM (memento bigger than free pool) → False ✓")


def test_release_pinned_memento_decrements_refcount():
    """Phase 4a: release_pinned_memento drops ref_cnt on the pinned blocks."""
    from types import SimpleNamespace
    api = _api()
    api["reset"]()

    # Mock blocks list with refcounts pre-incremented (as if pinned)
    mock_blocks = [SimpleNamespace(block_id=i, ref_cnt=0) for i in range(20)]
    pinned_ids = [5, 6, 7]
    for bid in pinned_ids:
        mock_blocks[bid].ref_cnt = 1   # simulate pin

    class _FreeQueue:
        def __init__(self):
            self.appended = []
        def append_n(self, blocks):
            self.appended.extend(blocks)

    pool = SimpleNamespace(
        blocks=mock_blocks,
        free_block_queue=_FreeQueue(),
        get_num_free_blocks=lambda: 100,
        get_new_blocks=lambda n: [],   # not used in this test
    )

    s = _MockScheduler()
    s.kv_cache_manager.block_pool = pool

    # Stash a pinned memento.
    store = api["global_memento_store"]()
    store.stash(api["StoredMemento"](
        memento_id="m_pin",
        request_id="orig",
        logical_range=(0, 48),
        physical_block_ids=pinned_ids,
        block_size=16,
        num_layers=0,
        cpu_kv=None,
        gpu_pinned_block_ids=list(pinned_ids),
    ))

    s.release_pinned_memento = api["Scheduler"].release_pinned_memento.__get__(s)
    ok, msg = s.release_pinned_memento("m_pin")
    assert ok is True, msg
    assert "released 3 pinned blocks" in msg
    # ref_cnt should be back to 0
    for bid in pinned_ids:
        assert mock_blocks[bid].ref_cnt == 0
    # 3 blocks freed back to queue (since ref_cnt now 0)
    assert len(pool.free_block_queue.appended) == 3
    # Tag cleared
    m = store.get("m_pin")
    assert m.gpu_pinned_block_ids is None

    # Calling again returns ok=False (already released).
    ok2, msg2 = s.release_pinned_memento("m_pin")
    assert ok2 is False
    assert "not GPU-pinned" in msg2

    api["reset"]()
    print("  release pinned memento → refcount drops, blocks return to queue ✓")


def test_release_pinned_memento_unknown_returns_false():
    api = _api()
    api["reset"]()
    s = _MockScheduler()
    s.release_pinned_memento = api["Scheduler"].release_pinned_memento.__get__(s)
    ok, msg = s.release_pinned_memento("never_stashed")
    assert ok is False
    assert "unknown" in msg
    api["reset"]()
    print("  release unknown memento → False ✓")


def test_two_restores_on_same_request_accumulate():
    api = _api()
    api["reset"]()
    store = api["global_memento_store"]()
    for mid in ("m_a", "m_b"):
        store.stash(api["StoredMemento"](
            memento_id=mid,
            request_id="orig",
            logical_range=(0, 32),
            physical_block_ids=[0, 1],
            block_size=16,
            num_layers=0,
            cpu_kv=None,
        ))
    s = _MockScheduler()
    s.queue_kv_restore = api["Scheduler"].queue_kv_restore.__get__(s)
    ok1, _ = s.queue_kv_restore(request_id="r", memento_id="m_a")
    ok2, _ = s.queue_kv_restore(request_id="r", memento_id="m_b")
    assert ok1 and ok2
    ops = s.pending_kv_restore_operations["r"]
    assert len(ops) == 2
    assert {o.memento_id for o in ops} == {"m_a", "m_b"}
    api["reset"]()
    print("  two restores on one request → both queued ✓")


if __name__ == "__main__":
    test_queue_kv_restore_unknown_memento_returns_false()
    test_queue_kv_restore_allocates_and_queues()
    test_queue_kv_restore_oom_returns_false()
    test_release_pinned_memento_decrements_refcount()
    test_release_pinned_memento_unknown_returns_false()
    test_two_restores_on_same_request_accumulate()
    print("ALL PHASE 3B + 4A API TESTS PASSED")
