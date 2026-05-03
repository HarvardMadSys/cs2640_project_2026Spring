# v3 Overlay Patches

The Phase 1 changes to the Memento vLLM overlay live here as a patch +
one new file, because `external/memento/` is gitignored (it's a clone of
microsoft/memento, not vendored).

## Contents

| File | Purpose |
|---|---|
| `v3_phase1_modifications.patch` | Unified diff against `microsoft/memento@d8c10e6`. Modifies 8 files in the overlay. |
| `memento_store.py.new` | New file — `MementoStore` singleton + dataclasses. Drop into `external/memento/vllm/vllm/v1/core/block_masking/memento_store.py`. |
| `apply.sh` | Idempotent applier. Run from repo root. |

## Apply

```bash
cd /path/to/adaptivecache-paper2

# Re-fetch the upstream Memento clone if missing.
[ -d external/memento ] || git clone --depth 1 https://github.com/microsoft/memento external/memento

# Pin the upstream commit our patch was authored against.
( cd external/memento && git fetch --depth 50 origin d8c10e6 && git checkout d8c10e6 )

# Apply our changes.
bash studies/lifetime_cost/paper2/v3_overlay_patches/apply.sh

# Re-install the overlay over the venv's vLLM.
cd external/memento/vllm && bash install_overlay.sh
```

## What Phase 1 buys you

After the overlay is installed and the engine started with
`auto_capture_mementos=True`, every block-masking compaction event also
copies the obs's GPU KV blocks into CPU pinned memory under a
deterministic memento_id (`auto:{request_id}:{start}:{end}`). The
captures are reachable via:

```python
from vllm.v1.core.block_masking import global_memento_store
store = global_memento_store()
print(store.memento_ids(), store.total_cpu_bytes())
```

Phase 1 captures only — no recall splice yet. See `V3_DESIGN.md` for the
full architecture and `tests/smoke_v3_capture.py` for end-to-end
validation (GPU required).
