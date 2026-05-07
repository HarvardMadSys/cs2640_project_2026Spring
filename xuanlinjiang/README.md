# Erasure Codes for Distributed Storage: Implementation and Trace-Driven Evaluation

**Author:** Xuanlin Jiang
**Course:** CS 2640 (Spring 2026)

## Abstract

This project implements a spectrum of erasure codes — RAID-0/1/6, systematic
Reed-Solomon, Minimum-Storage Regenerating (MSR), and Minimum-Bandwidth
Regenerating (MBR) — under a unified `ErasureCode` interface and evaluates them
with a discrete event simulator (DES) driven by Microsoft Research Cambridge
block I/O traces. The simulator captures I/O dependencies (read-modify-write,
parity computation, repair reconstruction) as a DAG of `DiskRequest`s with
`on_ready` callbacks, so the same code paths run for both correctness
verification and timing-only simulation. We measure load–latency curves under
three operating phases (normal, degraded, repair-in-progress) across a sweep of
arrival rates, and quantify the storage / repair-bandwidth / latency trade-offs
predicted by regenerating-code theory against measured behaviour.

## Repository Layout

| Path | Contents |
|------|----------|
| `report.pdf` | Compiled USENIX-format final report |
| `report/` | LaTeX sources (`report.tex`, `report.bib`, `usenix2019_v3.sty`) and figure assets under `report/imgs/` |
| `src/` | Pointer to the external source-code repository (see `src/README.md`) |

## Source Code

The implementation, build system, tests, experiment scripts and plotting code
live in a separate repository:

**https://github.com/SwiftSeal03/cs2640-proj**

See that repository's top-level `README.md` for full build and run
instructions. A short summary:

```bash
git clone https://github.com/SwiftSeal03/cs2640-proj.git
cd cs2640-proj
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel

./build/test/test_raid                       # RAID-0, RAID-1
./build/test/test_ec                         # RAID-6, RS, MSR, MBR

bash scripts/traces/process_traces.sh        # one-time trace pre-processing
bash scripts/experiments/run_all_sim.sh      # full sweep + plots
```

## Building the Report

```bash
cd report
pdflatex report.tex
bibtex   report
pdflatex report.tex
pdflatex report.tex
```

Figure assets are bundled under `report/imgs/`, so the build is
self-contained.

## Codes Implemented

| Code | Parameters | Storage overhead | Repair |
|------|------------|------------------|--------|
| RAID-0 | n=6 | 1× | none |
| RAID-1 | n=2 | 2× | mirror |
| RAID-6 | n=6, k=4 | 1.5× | any 2 disks (P+Q over GF(2⁸)) |
| Reed–Solomon | n=6, k=4 | 1.5× | any 2 disks (Cauchy MDS) |
| MSR | n=6, k=3, d=5 | 2× | layered RS, β = 1 symbol |
| MBR | n=6, k=3, d=5 | 2.5× | product-matrix, β = 1 symbol |

## Traces

[Microsoft Research Cambridge block I/O traces](https://iotta.snia.org/traces/block-io)
(Narayanan et al., 2007). Four workloads are replayed: `hm_0`, `hm_1`
(Home server) and `mds_0`, `mds_1` (Media/Dev server). The reported
experiments use `mds_1`.
