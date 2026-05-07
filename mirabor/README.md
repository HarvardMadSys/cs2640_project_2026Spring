# Cache Eviction on Congressional & Court APIs: Workload Shape and Cost Attribution Decide the Policy

**Author:** Mira Yu (Harvard, CS 2640: Modern Storage Systems, Spring 2026)

## Overview

A measurement study of cache eviction policies on real public-records APIs (Congress.gov, CourtListener), arguing that the right policy is determined by *what the operator pays for*, not by metric default. Public-records APIs are rate-limited at the origin (~5,000 req/hr), have no per-byte charge, span 335× size variance in a single namespace, and are TTFB-dominated (~358 ms p50). On these workloads, **object-miss is the deployment cost; byte-miss is not**, and that attribution flips the post-2020 "size-aware-is-dead" trend (Caffeine, S3-FIFO, SIEVE).

Across two real traces with multi-seed sweeps over four α, four cache fractions, and ten policies (LRU, FIFO, CLOCK, S3-FIFO, SIEVE, W-TinyLFU, GDSF, GDSF-Cost, LHD-Lite, LHD-Full):

- **GDSF wins object-miss on Court** (heavy-tail size) at `cache_frac ≥ 0.01`, 3–9 pp over W-TinyLFU; W-TinyLFU takes smaller caches.
- **LHD-Full wins on Congress** (light-tail size) at α=0.6 (1.8 pp, *n*=20, Welch *p* < 10⁻⁴); ties GDSF at α=0.8 (*p* = 0.65); GDSF wins at α ≥ 1.0.
- The SIEVE-vs.-W-TinyLFU gap on Court is driven by **size-outlier rejection**, not frequency-gradient capture, triple-confirmed by size/freq/matched-cardinality controls.
- For byte-priced backends (S3 egress) the verdict flips to W-TinyLFU on Court and SIEVE on Congress: same measurements, opposite winner.

## Folder contents

| Path | Purpose |
|------|---------|
| `report.pdf` | Compiled USENIX-format final report |
| `report/` | LaTeX source: `final.tex`, `usenix-2020-09.sty`, `figures/` |
| `src/` | Git submodule → [`mirabor/civicache`](https://github.com/mirabor/civicache): simulator, traces, analysis scripts |
| `ai-usage.md` | AI usage report (how I used AI, what I learned, what surprised me, tips) |
| `README.md` | This file |

## Build & run

### Get the source

```bash
git clone --recurse-submodules https://github.com/mirabor/cs2640_project_2026Spring.git
cd cs2640_project_2026Spring/mirabor
# or, if already cloned without submodules:
git submodule update --init --recursive
```

### Build the simulator

```bash
cd src
make            # builds cache_sim
```

### Reproduce the headline numbers

From `mirabor/src/`:

```bash
# Full sweep (4 α × 4 cache_frac × 10 policies × 2 traces)
python3 scripts/run_full_sweep.py

# 20-seed Welch t-test for the LHD-Full vs GDSF crossover on Congress
python3 scripts/congress_lhdfull_high_seed.py
# → results/congress_lhdfull_high_seed/summary.json
```

See `src/README.md` for the full set of analysis scripts and trace acquisition details.

### Compile the report

```bash
cd report
pdflatex final.tex
pdflatex final.tex   # second pass for cross-refs
```

## AI usage

See [`ai-usage.md`](./ai-usage.md).
