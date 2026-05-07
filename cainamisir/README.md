# AdaptiveCache: A Lifetime-Cost Study of Context Management for LLM Agents under Prefix-Cache-Aware Pricing

**Author:** Vlad Cainamisir (Harvard SEAS)
**Course:** CS2640 Modern Storage Systems, Spring 2026
**Final report:** [`report.pdf`](report.pdf) — also see [`report/final_report.tex`](report/final_report.tex) for source.
**AI usage disclosure:** [`ai-usage.md`](ai-usage.md).

## Abstract

LLM agents accumulate tens of thousands of tokens across long tool-use
trajectories. Modern serving systems exploit prefix caching, billing
already-computed tokens at roughly 1/10 the cost of uncached input.
Compaction policies that drop "stale" context to save tokens *break* the
prefix cache for everything past the mutation, forcing a re-prefill of
the suffix on the next call — a "cliff tax" that is large on real
trajectories. This project measures lifetime cost ($ per resolved task)
of eight training-free heuristic compaction policies on SWE-bench Lite
live and τ-bench retail with Anthropic Haiku 4.5, and finds that **no
policy Pareto-dominates the no-compaction baseline.** It then builds a
KV-pointer recall mechanism on a vLLM 0.13.0 overlay that captures
evicted observations to a CPU side cache, splices them back at recall
via in-place rotary-K rotation, and registers chain-hash entries
append-only — the storage-systems insight needed to make
"re-position cached blocks without re-prefilling" actually possible.
The mechanism is verified end-to-end on H100; the empirical Pareto
comparison against the baseline remains the missing piece, gated on
compute budget at submission time.

## Folder layout

```
cainamisir/
├── README.md       this file
├── report.pdf      compiled USENIX-format final report
├── report/         LaTeX source + figures + JSON figure specs
│   ├── final_report.tex
│   ├── figures/
│   ├── lifecycle_figure.png
│   ├── vllm_engineering.png
│   ├── paper2_architecture.json
│   └── paper2_kv_lifecycle.json
├── src/            pointer to source-code repositories (kept separate; large)
└── ai-usage.md     AI usage disclosure (final submission)
```

## Source code

The implementation lives in two GitHub repositories on the author's
account, kept separate so each can preserve its own commit history:

- **Paper 1 study (lifetime-cost framework + benchmarks + heuristic
  policies + cost analysis):**
  <https://github.com/cainamisir/adaptivecache> (branch `master`)

- **Paper 2 mechanism (vLLM 0.13.0 + Memento overlay + KV-pointer recall
  + scheduler patches):**
  <https://github.com/cainamisir/adaptivecache> (branch
  `paper2-memento-recall`)

Both branches share an `external/memento` submodule pinned to
microsoft/memento at `d8c10e6` plus our v3/v4/v8/v9 patches in
`studies/lifetime_cost/paper2/v3_overlay_patches/`.

See `src/README.md` for a one-page guide to navigating the codebase.

## Build / run

### Final report

```bash
cd report
pdflatex final_report.tex
bibtex final_report
pdflatex final_report.tex
pdflatex final_report.tex
```

Uses the USENIX 2020-09 LaTeX template; expects `usenix-2020-09.sty`
on the path (bundled in the repository this folder was authored in).

### Reproducing the experiments

See the in-repo project README at
[`src/adaptivecache_paper2/README.md`](src/adaptivecache_paper2/README.md)
(the most up-to-date version, on the `paper2-memento-recall` branch).
It covers:

- Paper 1 SWE-bench Lite live + τ-bench retail bake commands
- Paper 2 Modal H100 launch commands for `validate_recall.py` and the
  Phase-1A through Phase-9 smoke tests
- Microbench commands (RoPE composition, paged-KV in-place rotation)
- Common pitfalls (Modal image rebuild triggers, `PYTHONHASHSEED=1`
  determinism requirement, etc.)

## Headline result

*No compaction policy Pareto-dominates `none` on a strong agent at any
benchmark cell tested.* The dominant cost term is `input_uncached`:
each compaction event invalidates the downstream prefix cache, so the
next K input tokens get billed at the uncached rate. Per-event cliff
cost was measured at $0.10–$0.15 of new uncached input on Haiku 4.5,
with a 5+-step amortization horizon before break-even — but agent
trajectories average ~26 steps total, leaving the cliff structurally
unrecoverable on most realistic workloads.

The constructive direction is to *change the cost model itself* — make
the cached suffix blocks remain reachable and correct even after the
prompt prefix changes. The Paper 2 mechanism is verified end-to-end
on H100 (12 captures, 12 rotations covering 6939 blocks, ~1.55 GB
peak GPU KV returned to vLLM's free pool, mechanism overhead ≈2% of
vLLM wall). The empirical demonstration that the resulting cache is
genuinely cheaper than `none` is the missing comparison; we document
what would close the gap in §9 of the report.
