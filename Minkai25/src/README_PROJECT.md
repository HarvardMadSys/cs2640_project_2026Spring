# README_PROJECT — Learned Promotion Gate for S3-FIFO

This repository holds the code, traces, plots, and writeup for a course project
that asks one question:

> Does replacing S3-FIFO's hardcoded $S \to M$ promotion rule with a tiny online
> linear classifier deliver reliable miss-ratio improvements across heterogeneous
> cache workloads?

The headline answer (after `§14`) is "no on average, yes on a narrow but
identifiable class of cells." The full story — including what failed, what was
mismeasured early, and how the conclusion changed across iterations — is in
`ATTACK_PLAN.md`. This file is the navigational index: what each piece of code
does, which experiments produced which numbers, and how to reproduce them.

---

## 1. What is in this repo

```
ATTACK_PLAN.md          living lab notebook (§1–§15) — primary source of truth
final_report.tex        course writeup (S3-FIFO-L)
final_report.pdf        rendered PDF
CS2640_LearnedCaching.pptx   slides
references.md           bibliography pointers used in the writeup

plugins/                all project code (Python harness + algorithms)
├── sim.py                    self-contained simulator + trace registry
├── baselines.py              FIFO, LRU, LFU, S3FIFO (T=1 default), SIEVE,
│                              ARC, TwoQ, Belady (Python re-implementations)
├── learned_promotion.py      S3FIFOLearned, V2, V3, V4, V5, V6, V7, V8, V9 (GAM)
├── admission_stump.py        attack idea #8 (size-threshold admission filter)
├── exp3_sizer.py             attack idea #4ii (EXP3 over |S| arms)
├── reuse_distance.py         msr_prxy_0 reuse-distance histogram (§14 plot)
├── diag_v4.py                §15 weight-jitter / label-imbalance diagnostic
├── cachesim_runner.py        subprocess wrapper around libCacheSim's
│                              C `cachesim` binary; handles batching,
│                              chunking at 16 algos, name-prefix matching
├── convert_traces.py         CSV → oracleGeneral binary converter
│                              (also fills next_access_vtime via backward pass)
├── run_experiments.py        single-trace driver (auto-discovers attack
│                              modules, dispatches to cachesim where possible)
├── sweep14.py                drives run_experiments.py over all 15 traces
├── sweep14_report.py         pretty-prints the §14 cell-by-cell table
└── result/                   per-cell JSON outputs (sweep14_*.json,
                               diag_v4_*.json, msr_prxy_0_reuse_distance.{pdf,png})

scripts/                upstream libCacheSim Python helpers (mostly unused
                         here because the wheel won't build in this env)
libCacheSim/            upstream C++ simulator (we only build `cachesim`)
_build/bin/cachesim     the binary the harness shells out to

data/                   trace inputs (NOT committed — see §3 below)
figures/                publication-quality plots
```

The `_build/`, `cmake/`, `dockerfile`, `test*`, `example/`, etc. are vendored
from upstream libCacheSim and are unmodified except for one polyfill in
`libCacheSim/dataStructure/histogram.c` (`g_memdup2 → g_memdup` for older
glib). See ATTACK_PLAN.md §12 for the build path.

---

## 2. The contribution: S3-FIFO-L (V4)

**S3FIFOLearnedV4** (`plugins/learned_promotion.py:487`) is a logistic-regression
gate over **3 features** that replaces S3-FIFO's `freq >= T` promotion rule:

| feature | meaning |
|---|---|
| `log_hits`  | `log(1 + uncapped hit count since admission)` — replaces the 2-bit `freq` counter |
| `age`       | residency time in $S$, normalized by `S_cap` |
| `recency`   | virtual time since last hit, normalized by `S_cap` |

Online SGD, `lr=0.05`, `L2=1e-4`, label is the Belady-boundary indicator
`y = 1{next_access − now < cache_size}` (read from the
`next_access_vtime` field of `oracleGeneral` traces). A
Lykouris–Vassilvitskii-style robustness fallback reverts to the canonical
freq-based rule if rolling 1000-step accuracy drops below 55% — in practice it
**never fires on V4** (it did fire on V2 a few times; ATTACK_PLAN.md §9).

**V4 is the production-recommended variant**: 4 trainable parameters, 3
features, beats every alternative in the V3/V5/V6/V7/V8/V9 ablation
(ATTACK_PLAN.md §10).

`+OptS` meta-aliases sweep `S ∈ {0.01, 0.05, 0.10, 0.25}` and pick the best per
cell post-hoc. This is the headline configuration in §13/§14.

---

## 3. Trace inventory

All traces are in libCacheSim's `oracleGeneral` format (24-byte records,
zstd-compressed): `<uint32 ts, uint64 obj_id, uint32 size, int64 next_access_vtime>`.
None are committed — total ~3.6 GB and several files exceed GitHub's 100 MB
limit. They are pulled from
`https://cache-datasets.s3.amazonaws.com/cache_dataset_oracleGeneral/...`
ad-hoc via curl (no S3 credentials needed).

### Traces used in the §14 sweep (15 real traces, 28 effective cells)

| trace | domain | cache sizes | source |
|---|---|---|---|
| `twitter` (= cluster52) | Twitter Twemcache KV | 1k / 10k | local CSV → converted |
| `cluster10`             | Twitter (near-uniform; no signal) | 1k / 10k | S3 |
| `cluster26`             | Twitter (locality-rich) | 1k / 10k | S3 |
| `cluster45`             | Twitter (write-heavy stress) | 1k / 10k | S3 |
| `cluster50`             | Twitter (write-heavy peer of 45) | 1k / 10k | S3 |
| `msr_hm_0`              | MSR Cambridge block I/O (scan-heavy) | 1k / 10k | S3 |
| `msr_proj_0`            | MSR Cambridge block I/O | 1k / 10k | S3 |
| `msr_prxy_0`            | MSR (very-high-locality, V4 catastrophe cell) | 1k / 10k | S3 |
| `alibaba_110`           | Alibaba Block | 100 / 500 | S3 |
| `alibaba_185`           | Alibaba Block | 100 / 500 | S3 |
| `cloudphysics`          | CloudPhysics block I/O (local CSV) | 500 / 5000 | local |
| `w105`                  | CloudPhysics single-VM | 500 / 5000 | S3 |
| `wiki` (= wiki_2019t)   | Wikipedia CDN | 1k / 10k | S3 |
| `meta_reag`             | Meta CDN | 1k / 10k | S3 |
| `block1`                | Meta Tectonic storage | 1k / 10k | S3 |

(`cluster10` is excluded from win/loss tallies — every algorithm prints
`req_mr=0.5000`; the trace has no temporal structure.)

### Traces tried and not used

- `wiki_2019_u` — 404 at the obvious S3 prefix; replaced by `wiki_2019t`.

---

## 4. Experiments — one row per `ATTACK_PLAN.md` section

| § | Title | Headline finding | Code | Output |
|---|---|---|---|---|
| §8 | First small-scale results on twitter cluster52 | V4 (then "LearnedPromote") wins by 1.7 pp at cache=1000 | `run_experiments.py` + `learned_promotion.py` (V1) + `admission_stump.py` + `exp3_sizer.py` | inline tables |
| §9 | Cross-trace generalization: ARC + TwoQ added, 7 traces | V4 (V3 then) "wins on 5/5 real traces" — turned out to be against non-canonical S3FIFO(T=1) | added `ARC`, `TwoQ` to `baselines.py`; oracleGeneral binary reader; trace registry in `sim.py` | inline tables |
| §10 | Feature/model-class ablation V3→V9 | V4 dominates: 3 features, 4 params; V9 GAM hurts | V3–V9 in `learned_promotion.py` | inline tables |
| §11 | Next-step plan | M-eviction gate + LV combiner + mirror-descent sizer | (planning section, no new code) | — |
| §12 | libCacheSim integration via subprocess | found Python ARC was off by 6.7 pp; canonical S3FIFO uses T=2 (not T=1) | `cachesim_runner.py` | retracted earlier ARC numbers |
| §13 | Comprehensive sweep with canonical baselines (7 traces) | V4 vs canonical S3FIFO: 8/12 wins; against best static baseline less clear | first version of `sweep14.py` (then 7 traces) | `result/sweep14_*.json` for the original 7 |
| §14 | **Honest result**: 15-trace sweep with `OptS_T2` ablation | V4+OptS vs canonical S3FIFO: +1.23 pp / 21W. **Vs OptS_T2: −0.15 pp / 8W-7L-13T.** Almost all of V4's apparent win is the S sweep, not the learning. | `sweep14.py`, `sweep14_report.py`, `convert_traces.py` (CSV→bin so cachesim can run twitter/cloudphysics) | `result/sweep14_*.json` (all 15) |
| §15 | V4 learning diagnostics | Two real pathologies: (a) Belady-binary labels are 0.0–5% positive; some cells degenerate. (b) `w_loghits` shows unbounded monotone drift on large-cache cells; L2=1e-4 is ~3 orders of magnitude too weak. | `diag_v4.py` | `result/diag_v4_*.json`, `figures/msr_prxy_0_reuse_distance.{pdf,png}` |

### What the §14 win/loss table looks like (the central honest result)

Threshold for win/loss = 0.1 pp; mean Δ = `mean(baseline − V4+OptS)`, positive
favors the second name.

| comparison | wins | losses | ties | mean Δ (pp) |
|---|---|---|---|---|
| V4 vs S3FIFO(T=2) | 14 | 5 | 9 | +0.64 |
| V4+OptS vs S3FIFO(T=2) | 21 | 3 | 4 | +1.23 |
| **V4+OptS vs OptS_T2** | **8** | **7** | **13** | **−0.15** |
| V4 vs OptST | 3 | 16 | 9 | −0.98 |
| V4+OptS vs OptST | 5 | 10 | 13 | −0.39 |
| V4+OptS vs best classical | 8 | 12 | 8 | −0.72 |
| **OptS_T2 vs S3FIFO(T=2)** | **18** | **2** | **8** | **+1.38** |
| NoPromote vs S3FIFO(T=2) | 6 | 12 | 10 | +0.22 |

Three cells are bona-fide V4+OptS wins over OptS_T2: `cloudphysics 5000`
(−1.72 pp), `twitter 1000` (−1.01 pp), `msr_proj_0 1000` (−0.55 pp). One cell
inverts catastrophically: `msr_prxy_0 1000` (V4+OptS +8.23 pp worse than
OptS_T2). These four cells frame the writeup's conclusion.

---

## 5. Algorithms in the registry

### Implemented in Python (`baselines.py`, `learned_promotion.py`, etc.)

- `FIFO`, `LRU`, `LFU`, `SIEVE`, `S3FIFO`, `ARC`, `TwoQ`, `Belady`
- `S3FIFO+LearnedV1..V9` — V4 is the headline; V9 is a piecewise-linear GAM
- `S3FIFO+LearnedV4+S{0.01,0.05,0.10,0.25}` and `+OptS` meta-alias
- `S3FIFO+T{1,2,3}+S{0.01,0.05,0.10,0.25}` and `+OptT`/`+OptS`/`+OptST`
- `S3FIFO+NoPromote` — diagnostic: gate disabled, M fills only via ghost-hits
- `S3FIFO+Stump` (admission filter, idea #8 — clean negative result)
- `S3FIFO+EXP3` (sizer, idea #4ii — does not converge in 50 epochs)

### Dispatched to libCacheSim's `cachesim` binary

`FIFO, LRU, LFU, ARC, TwoQ, SIEVE, S3FIFO (T=2 canonical), Belady, LeCaR, LIRS,
Cacheus, ClockPro, Hyperbolic, WTinyLFU, GDSF, LHD, QDLP, SLRU`. The dispatcher
falls back to Python when a trace isn't on disk in oracleGeneral form
(synthetic Zipf, ad-hoc CSV slices) or when the algo is V*-family.

ARC and S3FIFO **must** go through cachesim — the Python ARC was 6.7 pp off
canonical (§12) and the Python S3FIFO defaults to non-canonical `T=1`.

---

## 6. Reproducing the experiments

Everything below assumes the working directory is the repo root.

### 6.1 One-time setup

```bash
# (a) build the cachesim binary (used by §12 onward).
#     One polyfill — at the top of libCacheSim/dataStructure/histogram.c
#     add `#define g_memdup2 g_memdup` for older glib (see ATTACK_PLAN.md §12).
module load cmake/4.2.3-fasrc01    # or any cmake >= 3.20 you have on PATH
cmake -G Ninja -B _build \
      -DENABLE_TESTS=OFF -DENABLE_LRB=OFF \
      -DENABLE_GLCACHE=OFF -DENABLE_3L_CACHE=OFF \
      -DOPT_SUPPORT_ZSTD_TRACE=ON -DCMAKE_BUILD_TYPE=Release
ninja -C _build cachesim
ls _build/bin/cachesim    # must exist before `cachesim_runner.py` will dispatch

# (b) Python deps. We do NOT need the libcachesim Python wheel
#     (which fails to build in some environments — see §7).
pip install zstandard numpy
```

### 6.2 Pulling traces

> **The `.zst` trace files exist on disk in this checkout but are not pushed
> to git.** They are listed as untracked in `git status` (see the project
> root's status block) and `.gitignore` excludes the data dir on intent —
> they total ~3 GB and several individual files exceed GitHub's 100 MB push
> limit (`wiki_2019t` is 1.9 GB; `meta_reag` is 335 MB; `block_traces_1` is
> 112 MB). If you are working in this same checkout, the data is already
> there:
>
> ```bash
> ls -lh data/*.zst data/*.bin
> ```
>
> Expected sizes (current local checkout):
>
> ```
> 1.2M  alibaba_alibabaBlock_110.oracleGeneral.zst
> 3.9M  alibaba_alibabaBlock_185.oracleGeneral.zst
> 112M  block_traces_1.oracleGeneral.bin.zst
> 2.7M  cloudPhysicsIO.oracleGeneral.bin
>  78M  cluster10.oracleGeneral.sample10.zst
>  72M  cluster26.oracleGeneral.sample10.zst
> 158M  cluster45.oracleGeneral.sample10.zst
>  92M  cluster50.oracleGeneral.sample10.zst
> 335M  meta_reag.oracleGeneral.zst
>  18M  msr_hm_0.oracleGeneral.zst
>  18M  msr_proj_0.oracleGeneral.zst
>  47M  msr_prxy_0.oracleGeneral.zst
>  62M  twitter_cluster52_10m.csv.zst
>  23M  twitter_cluster52.oracleGeneral.bin
>  12M  w105.oracleGeneral.bin.zst
> 1.9G  wiki_2019t.oracleGeneral.zst
> ```

If you are setting up a fresh checkout elsewhere, fetch them from the public,
no-credentials bucket
`https://cache-datasets.s3.amazonaws.com/cache_dataset_oracleGeneral/`.
The two prefixes verified in the source code (`plugins/s3fifo.py`,
`plugins/cacheus.py`, `plugins/sieve.py`):

| family | S3 prefix | filenames |
|---|---|---|
| Twitter Twemcache | `2020_twitter/` | `cluster{10,26,45,50}.oracleGeneral.sample10.zst` |
| MSR Cambridge | `2007_msr/` | `msr_{hm,proj,prxy}_0.oracleGeneral.zst` |

```bash
mkdir -p data && cd data
for f in cluster10 cluster26 cluster45 cluster50; do
  curl -O "https://cache-datasets.s3.amazonaws.com/cache_dataset_oracleGeneral/2020_twitter/${f}.oracleGeneral.sample10.zst"
done
for f in msr_hm_0 msr_proj_0 msr_prxy_0; do
  curl -O "https://cache-datasets.s3.amazonaws.com/cache_dataset_oracleGeneral/2007_msr/${f}.oracleGeneral.zst"
done
cd ..
```

The remaining files used in §14 — `alibaba_alibabaBlock_{110,185}.oracleGeneral.zst`,
`w105.oracleGeneral.bin.zst`, `wiki_2019t.oracleGeneral.zst`,
`meta_reag.oracleGeneral.zst`, `block_traces_1.oracleGeneral.bin.zst` — live
in the same bucket but their year/source subdirectories are not hard-coded in
this repo's source. Two ways to locate them:

1. Browse the bucket index without credentials:
   `aws s3 ls --no-sign-request s3://cache-datasets/cache_dataset_oracleGeneral/`
   then walk into the year/source prefix that matches the filename.
2. Cross-reference the
   [Thesys-lab `cacheMon/cache_dataset` catalog](https://github.com/cacheMon/cache_dataset)
   on GitHub, which maps each filename to its release subdirectory.

Drop each `.zst` into `data/` with the **exact** filename listed in
`plugins/cachesim_runner.py:36–55` — the harness keys traces off filename
literals, not pattern matches.

The two CSV traces (`twitter_cluster52.csv`, `cloudPhysicsIO.csv`) shipped
locally rather than from S3. They are present in this checkout but also not
redistributable through git. If you have them (or just the
`twitter_cluster52_10m.csv.zst` slice that *is* on disk here), run the
converter — `next_access_vtime` is reconstructed by a backward pass on disk,
so cachesim's Belady and V4's Belady label both work afterward:

```bash
python3.11 plugins/convert_traces.py
```

### 6.3 Quick-start: a single cell on one trace (≲30 s)

Smoke-test the harness end-to-end. This is the right starting point — if it
prints sensible numbers, the §14 sweep will work:

```bash
python3.11 plugins/run_experiments.py \
    --trace cluster45 \
    --sizes 1000 \
    --limit 200000 \
    --algos "S3FIFO,S3FIFO+LearnedV4,FIFO,LRU,SIEVE,ARC"
```

Expected: `S3FIFO+LearnedV4` ≈ 0.546, vanilla `S3FIFO(T=2)` ≈ 0.554, `FIFO`
≈ 0.574 (numbers from ATTACK_PLAN.md §13/§14).

`run_experiments.py` flags:
- `--trace NAME` — one of the keys in `plugins/sim.py:TRACES`
  (`twitter`, `cluster10/26/45/50`, `msr_hm_0/proj_0/prxy_0`,
  `alibaba_110/185`, `cloudphysics`, `w105`, `wiki`, `meta_reag`, `block1`)
- `--sizes N1,N2,...` — comma-separated cache sizes (default = a per-trace sweep)
- `--limit N` — first N requests only (default = full trace)
- `--algos A,B,C` — subset of the registry; supports meta-aliases
  `S3FIFO+OptT`, `+OptS`, `+OptST`, `S3FIFO+LearnedV4+OptS`
- `--quick` — 100k requests + a small fixed size set, for fast smoke tests
- `--json PATH` — also dump the per-cell numbers as JSON

### 6.4 The §14 cross-trace sweep (~13.5 min wall-clock, all 15 traces)

```bash
python3.11 plugins/sweep14.py            # full sweep
python3.11 plugins/sweep14_report.py     # pretty-print the cell-by-cell table
                                         # + win/loss tally vs S3FIFO/OptS_T2/OptST
```

Per-cell JSON lands in `plugins/result/sweep14_<trace>.json`. To re-run a
subset of traces only:

```bash
python3.11 plugins/sweep14.py twitter cluster45 cloudphysics
```

### 6.5 The §15 V4 weight/label diagnostics (~5 min for the full set)

```bash
# one cell — small slice, ~30 s
python3.11 plugins/diag_v4.py cluster45 1000 200000

# the 10 cells used in the §15 table (run sequentially)
for spec in "cluster10 1000 200000" "cluster10 10000 200000" \
            "alibaba_110 100 100000" "alibaba_185 100 100000" \
            "cloudphysics 500 100000" "cluster45 1000 200000" \
            "cluster45 10000 200000" "msr_hm_0 1000 200000" \
            "twitter 1000 200000" "twitter 10000 200000"; do
  python3.11 plugins/diag_v4.py $spec
done
```

Per-cell JSONs in `plugins/result/diag_v4_*.json` carry the snapshot
trajectory of `(w, b)`, per-window `y=1` rates, and the pathology stats
(`flips`, `std_2nd`, `all0w%`) summarized in the §15 table.

### 6.6 Reproducing earlier sections (§8–§13) explicitly

These sections all run through the same `run_experiments.py` driver, just with
narrower trace/algo sets. Each maps to a single CLI invocation:

| section | command |
|---|---|
| §8 (V1 vs vanilla S3FIFO + Stump + EXP3 on twitter cluster52) | `python3.11 plugins/run_experiments.py --trace twitter --sizes 1000,10000 --limit 500000 --algos "S3FIFO,S3FIFO+LearnedV1,S3FIFO+Stump,S3FIFO+EXP3,S3FIFO+S0.02,S3FIFO+S0.05,S3FIFO+S0.20,S3FIFO+S0.30"` |
| §9 (cross-trace V3 + ARC/TwoQ) | run §6.3 quick-start in a loop over the 7 trace names; uses Python ARC, NOT canonical |
| §10 (V3→V9 ablation) | `--algos "S3FIFO,S3FIFO+LearnedV3,...+LearnedV4,...+LearnedV5,...+LearnedV6,...+LearnedV7,...+LearnedV8,...+LearnedV9"` per trace |
| §12 (cachesim canonical baselines on cluster45) | `python3.11 plugins/run_experiments.py --trace cluster45 --sizes 1000 --algos "FIFO,LRU,LFU,ARC,TwoQ,SIEVE,S3FIFO,Belady,LeCaR,LIRS,Cacheus,WTinyLFU,GDSF,LHD,QDLP,SLRU"` (cachesim auto-dispatch) |
| §13 (7-trace canonical sweep) | the same `sweep14.py` invocation but only `twitter cluster10 cluster45 msr_hm_0 cloudphysics wiki alibaba_185` as args |
| §14 (15-trace canonical sweep) | §6.4 |
| §15 (V4 diagnostics) | §6.5 |

### 6.7 The `msr_prxy_0` reuse-distance figure

```bash
python3.11 plugins/reuse_distance.py
# writes figures/msr_prxy_0_reuse_distance.{pdf,png}
# and result/msr_prxy_0_reuse_distance.{pdf,png}
```

---

## 7. Known limitations / things that would need fixing

- **Python SIEVE is `O(n)` per eviction** in `baselines.py` (rebuilds the key
  list each call). Fine at cache≤10k, dominates wall-clock at full scale; was
  obsoleted by the cachesim path in §12, but the Python implementation is
  still in the registry for non-binary traces.
- **Python ARC is 6.7 pp off canonical** at cluster45 cache=1000 — left in for
  the CSV-only traces but should not be used for any reported number. Always
  prefer the cachesim ARC.
- **`libcachesim` Python wheel does not build** in this environment (CMake
  failure in trace-analyzer C++ subobjects). All evaluation is done either
  through the standalone `cachesim` binary or through the in-tree Python
  re-implementations in `baselines.py`.
- **`s3fifod`, `fifo-reinsertion`, `lfu_da*`** are not invocable from
  cachesim's CLI in this build (the parser routes them to the admission-algo
  path). They are absent from the §14 baseline set.
- **cachesim segfaults at ≥17 algos in one invocation** (likely a fixed-size
  array). `cachesim_runner.py` chunks at 16.
- **Some byte-MR claims are not yet evaluated** — most runs use
  `ignore_obj_size=True`, so `log_size` is constant and the V4 weight on it
  collapses to bias. Wiki and Meta CDN cells could surface real size signal but
  weren't re-run with sizes on.

---

## 8. Pointers between this README, ATTACK_PLAN, and the writeup

- **What experiment does paper section X correspond to?** The writeup's
  "First Results" section maps to ATTACK_PLAN.md §8/§9; "The Full Result"
  section maps to ATTACK_PLAN.md §13/§14; "Future Work" inherits items from
  ATTACK_PLAN.md §11 + §15.
- **Where do the per-cell numbers in `final_report.tex` come from?**
  `plugins/result/sweep14_*.json` (one file per trace, each with a nested
  `{cache_size: {algo: {req_mr, byte_mr, time}}}`).
- **Where does the `msr_prxy_0` reuse-distance figure come from?**
  `plugins/reuse_distance.py` produces both the PDF and PNG in `figures/` and
  `plugins/result/`.
- **Why does ATTACK_PLAN.md §13 contradict §14 in places?** §13 was written
  before the `OptS_T2` ablation was computed. §14 is the corrected and current
  view; §13 is preserved for the audit trail.
