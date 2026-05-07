# Source code

The implementation of this project is large (~150K lines across two
research repos plus a vendored vLLM 0.13.0 fork) so it is kept in
external GitHub repositories rather than bundled into the course
submission. Both repos preserve full per-commit history for the
semester.

## Repositories

**`cainamisir/adaptivecache`** — single repo with two branches:

- **`master`** — Paper 1 study (lifetime-cost framework, benchmarks,
  eight heuristic compaction policies, cost decomposition figures,
  USENIX-format final report). Path of interest: `studies/lifetime_cost/`.

- **`paper2-memento-recall`** — Paper 2 study (vLLM 0.13.0 + Memento
  overlay + KV-pointer recall + scheduler patches). Paths of interest:
  `studies/lifetime_cost/paper2/`, `studies/lifetime_cost/paper2/v3_overlay_patches/`,
  `external/memento/` (cloned at build time on Modal, `d8c10e6` + our
  v3/v4/v8/v9 patches).

Clone:

```bash
git clone https://github.com/cainamisir/adaptivecache.git
cd adaptivecache
git checkout master                  # Paper 1
# or
git checkout paper2-memento-recall   # Paper 2
```

## Build / run

See [`adaptivecache_paper2/README.md`](adaptivecache_paper2/README.md)
in this folder — the most up-to-date project README, taken from the
`paper2-memento-recall` branch. It covers:

- Paper 1 SWE-bench Lite live + τ-bench retail bake commands
  (`scripts/run_experiment_matrix.py`, `scripts/run_swebench.py`)
- Paper 2 Modal H100 launch commands for `validate_recall.py` and the
  Phase-3 / Phase-4 / Phase-9 smoke tests
- Microbench commands (RoPE composition, paged-KV in-place rotation)
- Common pitfalls (Modal image rebuild triggers,
  `PYTHONHASHSEED=1` determinism requirement, `--no-pin` natural
  eviction mode, etc.)

## Why two branches in one repo

Paper 2 builds on Paper 1's framework but ships an order-of-magnitude
more code (the vLLM overlay, Modal containers, scheduler patches,
microbenches). Keeping them on separate branches of the same repo
makes the shared library code (`src/`, `studies/lifetime_cost/pipeline/`)
diff-trackable while letting Paper 2's much larger surface area
evolve independently. Both branches share the same ancestry up to the
point of divergence.

## Reproducibility

Paper 1 results are reproducible from a single Anthropic API key; no
GPU required. Paper 2 results require a Modal account with H100 access
and an Anthropic key (Anthropic Haiku 4.5 generates the textual
mementos at compaction time). Wall-clock for the full Paper 2 sweep at
N=10 was estimated at ~3 hours of H100 time; we ran it at smaller N due
to compute budget — the report is honest about this limit in §8.
