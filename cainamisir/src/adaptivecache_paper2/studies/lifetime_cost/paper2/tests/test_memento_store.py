"""Unit tests for the Phase 1A memento store API surface.

These tests exercise the data classes and the singleton MementoStore
without needing a GPU. The actual GPU→CPU capture is exercised by
`smoke_v3_capture.py` (GPU-required).

Run:
    cd /home/vlad/adaptivecache-paper2
    .venv-paper2/bin/python -m studies.lifetime_cost.paper2.tests.test_memento_store
"""
from __future__ import annotations


def _import_store():
    """Import after ensuring the overlay's vllm path is on sys.path.

    The test runs against whatever vllm is currently installed/overlaid;
    the overlay must be installed (install_overlay.sh) for the new
    block_masking.memento_store module to exist.
    """
    from vllm.v1.core.block_masking.memento_store import (
        StoredMemento,
        MementoStore,
        CaptureSpec,
        CaptureOp,
        global_memento_store,
        reset_global_memento_store,
    )
    return {
        "StoredMemento": StoredMemento,
        "MementoStore": MementoStore,
        "CaptureSpec": CaptureSpec,
        "CaptureOp": CaptureOp,
        "global_memento_store": global_memento_store,
        "reset_global_memento_store": reset_global_memento_store,
    }


def test_singleton_returns_same_instance():
    api = _import_store()
    api["reset_global_memento_store"]()
    a = api["global_memento_store"]()
    b = api["global_memento_store"]()
    assert a is b
    api["reset_global_memento_store"]()


def test_stash_get_evict_roundtrip():
    api = _import_store()
    api["reset_global_memento_store"]()
    store = api["global_memento_store"]()

    m = api["StoredMemento"](
        memento_id="m_test_42",
        request_id="req_xyz",
        logical_range=(100, 200),
        physical_block_ids=[5, 6, 7],
        block_size=16,
        num_layers=32,
        cpu_kv=None,  # metadata-only; no actual bytes (Phase 1A)
    )
    store.stash(m)
    assert len(store) == 1
    assert "m_test_42" in store.memento_ids()

    got = store.get("m_test_42")
    assert got is not None
    assert got.memento_id == "m_test_42"
    assert got.logical_range == (100, 200)
    assert got.physical_block_ids == [5, 6, 7]

    assert store.get("nope") is None

    assert store.evict("m_test_42") is True
    assert store.evict("m_test_42") is False  # already gone
    assert len(store) == 0
    api["reset_global_memento_store"]()


def test_capture_spec_and_op_dataclasses():
    api = _import_store()
    spec = api["CaptureSpec"](
        memento_id="m1",
        physical_positions=[100, 101, 102, 103],
        logical_range=(100, 104),
    )
    assert spec.memento_id == "m1"
    assert len(spec.physical_positions) == 4

    op = api["CaptureOp"](
        memento_id="m1",
        request_id="r1",
        src_block_ids=[6, 7],
        block_size=16,
        logical_range=(100, 104),
    )
    assert op.src_block_ids == [6, 7]
    assert op.block_size == 16


def test_cpu_bytes_zero_when_metadata_only():
    api = _import_store()
    m = api["StoredMemento"](
        memento_id="m_meta",
        request_id="r",
        logical_range=(0, 10),
        physical_block_ids=[1],
        block_size=16,
        num_layers=1,
        cpu_kv=None,
    )
    assert m.cpu_bytes() == 0


def test_cpu_bytes_counts_tensor_storage():
    """If the worker hook ran, cpu_kv would hold real tensors. Simulate that."""
    api = _import_store()
    try:
        import torch
    except ImportError:
        print("torch not available; skipping cpu_bytes tensor test")
        return
    m = api["StoredMemento"](
        memento_id="m_real",
        request_id="r",
        logical_range=(0, 16),
        physical_block_ids=[1],
        block_size=16,
        num_layers=2,
        cpu_kv=[
            torch.zeros(1, 2, 16, 8, 64, dtype=torch.bfloat16),
            torch.zeros(1, 2, 16, 8, 64, dtype=torch.bfloat16),
        ],
    )
    # bf16 = 2 bytes; 1*2*16*8*64 = 16384 elements per layer * 2 bytes = 32768 bytes
    # Two layers → 65536
    assert m.cpu_bytes() == 65536


if __name__ == "__main__":
    test_singleton_returns_same_instance()
    test_stash_get_evict_roundtrip()
    test_capture_spec_and_op_dataclasses()
    test_cpu_bytes_zero_when_metadata_only()
    test_cpu_bytes_counts_tensor_storage()
    print("ALL MEMENTO STORE TESTS PASSED")
