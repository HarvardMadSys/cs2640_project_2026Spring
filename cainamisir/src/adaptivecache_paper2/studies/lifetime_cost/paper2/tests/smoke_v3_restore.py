"""Phase 3a smoke: roundtrip capture+restore on dummy KV caches.

Validates that `restore_pinned_kv` correctly copies CPU pinned bytes back
to GPU at specified target block IDs. Uses dummy KV caches and a stashed
StoredMemento with a known pattern, so we can byte-compare after.

This test does not need a real vLLM engine — only torch + CUDA. It mocks
the `self.kv_caches` attribute that the restore method reads.

Run (needs GPU + torch):
    cd /home/vlad/adaptivecache-paper2
    .venv-paper2/bin/python -m studies.lifetime_cost.paper2.tests.smoke_v3_restore
"""
from __future__ import annotations

import os
import sys
import types

os.environ.setdefault("VLLM_LOGGING_LEVEL", "WARNING")


def main() -> int:
    import torch
    if not torch.cuda.is_available():
        print("CUDA not available — skipping (test requires GPU).")
        return 0

    from vllm.v1.core.block_masking.memento_store import (
        global_memento_store, reset_global_memento_store, StoredMemento,
    )
    from vllm.v1.worker.gpu_model_runner import GPUModelRunner

    reset_global_memento_store()

    # Tiny KV cache fixture matching FlashInfer layout
    # [num_blocks, 2, block_size, num_kv_heads, head_size]
    n_blocks = 8
    n_layers = 2
    block_size = 16
    n_heads = 4
    head_dim = 32

    print(f"Building dummy KV caches: {n_layers} layers × "
          f"[{n_blocks}, 2, {block_size}, {n_heads}, {head_dim}] FlashInfer layout")
    gpu_kv = [
        torch.zeros(n_blocks, 2, block_size, n_heads, head_dim,
                    dtype=torch.bfloat16, device="cuda")
        for _ in range(n_layers)
    ]

    # Build a known-pattern CPU tensor: layer L → fill value L+1.
    # Capture used physical_block_ids = [0, 1] (2 blocks).
    captured_n = 2
    cpu_kv = []
    for layer_idx in range(n_layers):
        t = torch.full(
            (captured_n, 2, block_size, n_heads, head_dim),
            float(layer_idx + 1),
            dtype=torch.bfloat16,
        ).pin_memory()
        cpu_kv.append(t)

    store = global_memento_store()
    store.stash(StoredMemento(
        memento_id="test_roundtrip",
        request_id="r0",
        logical_range=(0, captured_n * block_size),
        physical_block_ids=[0, 1],
        block_size=block_size,
        num_layers=n_layers,
        cpu_kv=cpu_kv,
    ))
    print(f"Stashed memento_id='test_roundtrip': {captured_n} blocks × "
          f"{n_layers} layers, {store.total_cpu_bytes()} bytes")

    # Mock a runner with self.kv_caches.
    class MockRunner:
        pass
    runner = MockRunner()
    runner.kv_caches = gpu_kv
    runner.restore_pinned_kv = types.MethodType(
        GPUModelRunner.restore_pinned_kv, runner
    )

    target = [4, 5]  # write into GPU blocks 4, 5 (different from source)
    print(f"Calling restore_pinned_kv -> target blocks {target}")
    ok = runner.restore_pinned_kv("test_roundtrip", target)
    assert ok, "restore_pinned_kv returned False"

    # Verify byte-exact content at target slots.
    for layer_idx in range(n_layers):
        actual = gpu_kv[layer_idx][target]  # shape: [2, 2, block_size, ...]
        expected = torch.full(
            actual.shape, float(layer_idx + 1),
            dtype=torch.bfloat16, device="cuda",
        )
        if not torch.equal(actual, expected):
            print(f"FAIL layer {layer_idx}: actual mean={actual.float().mean().item()} "
                  f"expected mean={expected.float().mean().item()}")
            return 1
        print(f"  layer {layer_idx}: target slots match expected pattern "
              f"(value={layer_idx + 1.0})")

    # Negative case: nonexistent memento_id should return False, not crash.
    ok2 = runner.restore_pinned_kv("nonexistent", [6])
    assert ok2 is False
    print(f"  negative case: missing memento_id correctly returned False")

    # Verify the original blocks 0, 1 are STILL untouched (we wrote to 4, 5).
    for layer_idx in range(n_layers):
        unchanged = gpu_kv[layer_idx][[0, 1]]
        if unchanged.abs().sum().item() != 0.0:
            print(f"FAIL layer {layer_idx}: source blocks were modified "
                  f"(sum={unchanged.abs().sum().item()})")
            return 1
    print(f"  source blocks 0,1 untouched (we wrote to target 4,5 only)")

    print()
    print("PHASE 3A SMOKE PASS: capture/restore roundtrip is byte-exact.")
    reset_global_memento_store()
    return 0


if __name__ == "__main__":
    sys.exit(main())
