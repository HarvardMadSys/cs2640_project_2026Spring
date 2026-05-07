# Cache Policy Simulator for Legislative API Workloads

**CS 2640 Final Project**

Compares cache eviction policies — LRU, FIFO, CLOCK, S3-FIFO, SIEVE, W-TinyLFU, GDSF, and LHD — on synthetic Zipf workloads and real Congress.gov + CourtListener API traces. Includes SHARDS-based miss ratio curve (MRC) construction for efficient cache sizing analysis.

## Policies

| Policy | Description |
|--------|-------------|
| **LRU** | Least Recently Used — doubly-linked list + hash map |
| **FIFO** | First-In First-Out — no promotion on cache hit |
| **CLOCK** | Approximation of LRU with visited bits and a clock hand |
| **S3-FIFO** | Small FIFO (10%) + Main FIFO (90%) + ghost filter with 2-bit frequency counters |
| **SIEVE** | Lazy promotion with a roving hand pointer (NSDI '24) |
| **W-TinyLFU** | Tiny LFU admission filter + 1% window LRU + 99% SLRU; Count-Min Sketch frequency tracking |
| **GDSF** | Greedy-Dual Size Frequency — priority-queue eviction by frequency / size + clock |
| **LHD / LHD-Full** | Least Hit Density — eviction by predicted hit-density (NSDI '18) |

Ablation variants (selectable via `--policies`): `s3fifo-5/10/20`, `sieve-noprom`, `wtinylfu-dk`, `gdsf-cost`.

## Build

Requires a C++17 compiler. No external C++ dependencies.

```bash
make          # builds cache_sim
make test     # builds + runs W-TinyLFU and Doorkeeper acceptance suites
```

## Run

### Synthetic benchmark (default)

```bash
make run
# or directly:
./cache_sim
```

This generates a 500K-request Zipf trace (alpha=0.8, 50K objects) and runs all six policies at varying cache sizes.

### Full analysis with alpha sweep and SHARDS

```bash
make run-sweep
```

### Live demo (60s, 6-policy sweep)

```bash
./demo.sh
```

Runs `./cache_sim` on a pre-generated 5K-request slice of the Congress trace
with all 6 policies (LRU, FIFO, CLOCK, S3-FIFO, SIEVE, W-TinyLFU) at 4 cache
sizes, prints the miss-ratio table, and renders `results/demo/figures/mrc.pdf`.
See `demo-rehearsal.log` for timing evidence; completes in under 60 seconds.

### CLI options

```
./cache_sim [options]
  --trace <file>         Use a trace CSV instead of synthetic generation
  --limit <n>            Truncate trace after N requests (0 = no limit)
  --emit-trace <file>    Generate a synthetic trace, write to <file>, exit
  --cache-sizes <list>   Comma-separated cache size fractions (e.g., 0.01,0.05,0.1)
  --policies <list>      Comma-separated; see make_policy in src/main.cpp for the
                         full set: lru, fifo, clock, s3fifo, sieve, wtinylfu, gdsf,
                         lhd, lhd-full (+ ablation variants)
  --output-dir <dir>     Output directory (default: results)
  --num-requests <n>     Number of synthetic requests (default: 500000)
  --num-objects <n>      Number of unique objects (default: 50000)
  --seed <n>             RNG seed for Zipf trace + replay (default: 42)
  --alpha <a>            Zipf alpha for single run (default: 0.8)
  --alpha-sweep          Run alpha sensitivity sweep (0.6 to 1.2)
  --size-mu <m>          Synthetic log-normal size mu (default: 8.3)
  --size-sigma <s>       Synthetic log-normal size sigma (default: 1.5)
  --shards               Run SHARDS MRC construction
  --shards-rates <list>  Comma-separated SHARDS rates (default: 0.001,0.01,0.1)
  --shards-exact         Also compute exact stack distances (slow)
  --replay-zipf          Resample a real trace with Zipf popularity (use with --trace)
```

### Collect a real trace from Congress.gov

Requires a [Congress.gov API key](https://api.congress.gov/).

```bash
export CONGRESS_API_KEY=your_key_here
python3 scripts/collect_trace.py --requests 5000 --output traces/congress_trace.csv
```

Then run the simulator on it. Use `--replay-zipf` to overlay Zipf popularity on real keys/sizes:

```bash
./cache_sim --trace traces/congress_trace.csv --replay-zipf --alpha-sweep --shards
```

### Generate plots

Requires Python 3 with dependencies listed in `requirements.txt`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
make plots
# Figures saved to results/figures/
```

## Output

Results are written to `results/` as CSV files:

- `mrc.csv` — miss ratio vs. cache size for all policies
- `alpha_sensitivity.csv` — miss ratio vs. Zipf alpha
- `one_hit_wonder.csv` — OHW ratio at different window lengths
- `shards_mrc.csv` — SHARDS approximate MRC at multiple sampling rates
- `exact_mrc.csv` — exact MRC (if `--shards-exact` was used)

Plots are saved to `results/figures/` as PDFs.

## Project Structure

```
├── include/
│   ├── cache.h              # Eviction policies: LRU/FIFO/CLOCK/S3-FIFO/SIEVE/GDSF/LHD/LHD-Full
│   ├── wtinylfu.h           # W-TinyLFU (Caffeine-style, CMS admission)
│   ├── count_min_sketch.h   # Count-Min Sketch with conservative update
│   ├── doorkeeper.h         # Doorkeeper Bloom filter (ablation)
│   ├── hash_util.h          # Shared hashing helpers
│   ├── trace_gen.h          # Trace generation and loading
│   ├── workload_stats.h     # Workload characterization
│   └── shards.h             # SHARDS MRC construction
├── src/
│   ├── main.cpp             # CLI driver
│   ├── trace_gen.cpp        # Zipf generator + trace I/O
│   ├── workload_stats.cpp   # Alpha estimation (MLE), OHW ratio
│   └── shards.cpp           # SHARDS spatial sampling
├── tests/
│   ├── test_wtinylfu.cpp    # W-TinyLFU acceptance suite
│   └── test_doorkeeper.cpp  # Doorkeeper acceptance suite
├── scripts/
│   ├── collect_trace.py        # Congress.gov API trace collector
│   ├── collect_court_trace.py  # CourtListener REST v4 collector
│   ├── compare_workloads.py    # Multi-seed aggregation + Welch's t-test
│   ├── plot_results.py         # matplotlib figure generation
│   └── …                       # per-phase analysis & ablation tooling
├── traces/                  # Trace CSVs (most gitignored; demo + collected slices kept)
├── results/                 # CSV outputs + figures (regenerable)
├── demo.sh                  # Live 60s 6-policy sweep demo
├── requirements.txt         # Python deps for plotting + analysis scripts
├── Makefile
└── README.md
```

