"""
Monkey-patch hooks for vLLM's BlockSpaceManager to track real KV-cache
block counts per request.

WHY: tracer.py uses prompt_tokens as a proxy for KV-cache size. The real
measurement requires reading block allocations directly from vLLM's internal
BlockSpaceManager. This module patches allocate() and free() on that class to
maintain a per-request block count, which the tracer can query at each
tool-call boundary.

When vLLM is not installed (e.g. on the login node running analysis scripts),
all functions return 0 as a safe no-op.

IMPORTANT: vLLM's internal API is not stable. The BlockSpaceManager class
path has changed across versions:
  vLLM < 0.4:  vllm.core.block_manager.BlockSpaceManager
  vLLM >= 0.4: vllm.core.block_manager_v1.BlockSpaceManagerV1
               or vllm.core.block_manager_v2.BlockSpaceManagerV2
Check `python -c "import vllm; print(vllm.__version__)"` and grep the
installed vllm source with `grep -r "class BlockSpaceManager" $(python -c
"import vllm, os; print(os.path.dirname(vllm.__file__))")` to find the
correct path for your installed version.

Usage:
    from agent.vllm_hooks import vllm_block_tracking, get_kv_tokens
    with vllm_block_tracking():
        trace = run_task(task, model)
"""

import threading
from contextlib import contextmanager

_lock = threading.Lock()
_block_counts: dict = {}  # request_id -> int
_original_allocate = None
_original_free = None
_manager_cls = None


def _find_block_manager_cls():
    """Locate vLLM's BlockSpaceManager class across known version locations."""
    candidates = [
        ("vllm.core.block_manager", "BlockSpaceManager"),
        ("vllm.core.block_manager_v1", "BlockSpaceManagerV1"),
        ("vllm.core.block_manager_v2", "BlockSpaceManagerV2"),
    ]
    for module_path, class_name in candidates:
        try:
            import importlib
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name, None)
            if cls is not None:
                return cls
        except ImportError:
            continue
    return None


def patch_vllm_block_manager():
    """
    Monkey-patch BlockSpaceManager.allocate() and free() to record per-request
    block counts in _block_counts. Idempotent if already patched.
    """
    global _original_allocate, _original_free, _manager_cls

    cls = _find_block_manager_cls()
    if cls is None:
        return  # vLLM not installed or class not found

    if _original_allocate is not None:
        return  # already patched

    _manager_cls = cls
    _original_allocate = cls.allocate
    _original_free = cls.free

    def _patched_allocate(self, seq_group, num_lookahead_slots=0):
        result = _original_allocate(self, seq_group, num_lookahead_slots)
        try:
            request_id = seq_group.request_id
            block_count = sum(
                len(self.block_tables.get(seq.seq_id, []))
                for seq in seq_group.seqs_dict.values()
            )
            with _lock:
                _block_counts[request_id] = block_count
        except Exception:
            pass
        return result

    def _patched_free(self, seq):
        result = _original_free(self, seq)
        try:
            with _lock:
                _block_counts.pop(getattr(seq, "seq_id", None), None)
        except Exception:
            pass
        return result

    cls.allocate = _patched_allocate
    cls.free = _patched_free


def unpatch_vllm_block_manager():
    """Remove monkey-patches, restoring original methods."""
    global _original_allocate, _original_free, _manager_cls
    if _manager_cls is None or _original_allocate is None:
        return
    _manager_cls.allocate = _original_allocate
    _manager_cls.free = _original_free
    _original_allocate = None
    _original_free = None
    _manager_cls = None
    with _lock:
        _block_counts.clear()


def get_kv_tokens(request_id: str, block_size: int = 16) -> int:
    """
    Return the current KV-cache size in tokens for a given request.
    Block size defaults to 16 (vLLM default). Returns 0 if not tracked.
    """
    with _lock:
        return _block_counts.get(request_id, 0) * block_size


@contextmanager
def vllm_block_tracking():
    """Context manager: patch on enter, unpatch on exit."""
    patch_vllm_block_manager()
    try:
        yield
    finally:
        unpatch_vllm_block_manager()
