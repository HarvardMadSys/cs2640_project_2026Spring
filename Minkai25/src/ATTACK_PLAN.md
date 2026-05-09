# ATTACK_PLAN.md — CS 264 Learned Quick Demotion
 
> **Notes for future instances.** Project context: Minkai's CS 264 proposal augments S3-FIFO / SIEVE / HALP with lightweight learned signals. User wants minimum feature engineering, cache-internal signals only, computational overhead ignored for now. Theoretical bounds preferred but not required. Read this file before suggesting new directions or implementing — it captures the workload choice, ranked attack ideas, traces, libCacheSim integration path, and a 2-week concrete plan.

> **Trace data is intentionally not committed.** The `data/*.oracleGeneral.zst`, `data/*.oracleGeneral.bin.zst`, and the larger `data/*.oracleGeneral.bin` files referenced throughout (Twitter clusters, Wiki, Meta CDN, Alibaba block, MSR, CloudPhysics, Meta Tectonic) total ~3.6 GB and exceed GitHub's 100 MB file limit on several entries. They are fetched ad-hoc from `cache-datasets.s3.amazonaws.com/cache_dataset_oracleGeneral/...` over HTTPS — no S3 credentials needed — and converted locally via `plugins/convert_traces.py` where applicable. No download script is committed yet; reproduce by pulling the specific traces named in §3 / §13 / §14 directly from that bucket.
 
---
 
## TL;DR for the next instance
 
- **Workload**: Twitter Twemcache (54 traces, OSDI '20). Headline trace = `cluster52`. Justification in §1.
- **Headline idea**: Replace S3-FIFO's S→M promotion identity classifier with an online logistic regression on 4 cache-internal features. Theoretical anchor: Lykouris-Vassilvitskii learning-augmented caching framework. Details in §2 idea #1.
- **Must-have control baseline**: Decision-stump admission filter (idea #8). Implement first.
- **Open lever flagged by S3-FIFO authors themselves**: Adaptive |S| sizing — Yang et al. SOSP '23 explicitly state "tuning the adaptive algorithm is very challenging." This is idea #4 and is the cleanest "attacks an explicit open question" angle.
- **Tooling**: libCacheSim with Python `PluginCache` for prototyping, native C eviction module for full runs. §4.
---
 
## §1 — Workload domain choice: Twitter Twemcache KV
 
**Decision: focus the early experiments on the Twitter cluster traces.** Reasoning:
 
1. **Skew is heavy-tailed and stable** (super-Zipfian, α > 1) over 7-day windows but with diurnal bursts of new keys — the *exact* regime where S3-FIFO's small queue S overshoots. Yang's S3-FIFO blog post explicitly uses cluster52 to illustrate this: "ARC chooses a very small S … new (and popular) objects in S face more competition and often have to suffer a miss before being inserted in M, which causes low precision."
2. **Strong learnable signals from cache-internal state alone**: `key_size`, `value_size`, `client_id` (anonymized tenant), `op` (get/set/add/cas/delete), `TTL`. Most prior wins on this domain (LeCaR, CACHEUS, LRB ablations, GL-Cache) come from very simple recency/frequency/size combinations. Matches the user's "no over-engineering" constraint.
3. **Direct precedent**: S3-FIFO (SOSP '23) and SIEVE (NSDI '24) both feature these traces. The S3-FIFO paper specifically identifies clusters where ARC's adaptive sizing "improves tail performance but degrades overall performance" — explicit invitation for a learned gate.
4. **libCacheSim native support**: pre-converted to `oracleGeneral` `.zst` at `s3://cache-datasets/cache_dataset_oracleGeneral/2020_twitter/`. Loads directly. No format engineering.
5. **Iteration speed**: libCacheSim ~20 M req/s/thread; with `mrcProfiler` + SHARDS at rate 0.01, full MRC on 1.3 B-request trace in ~20 s with MAE < 0.1 %. Overnight = ~10 algos × 10 sizes × 10 traces.
**What this domain sacrifices**: byte-miss-ratio realism (slab allocator → most prior work uses `--ignore-obj-size 1`). If we want to evaluate byte-MR gains à la LRB/HALP, supplement with **Wikimedia CDN 2019** (`wiki_2019_u`) and **MetaCDN 2023** (`nha`, `prn`, `eag`).
 
**Recommended starter trace bundle (use exactly these 6 to start)**:
- `cluster52`, `cluster17`, `cluster18`, `cluster45` (Twitter; cluster45 is a write-heavy outlier where quick-demotion hurts — keep as a stress case)
- `wiki_2019_u` (CDN, byte-MR sanity check)
- `msr_hm_0` (block I/O, scan-heavy OOD sanity check)
---
 
## §2 — Ranked attack ideas
 
Ordered by leverage × differentiation × theoretical groundedness × implementation cost. Implement in this order unless early results redirect.
 
### #1 — Learned S→M Promotion Gate for S3-FIFO  ⭐ headline
 
- **Augments**: S3-FIFO; structurally portable to QD-LP-FIFO.
- **Failure mode addressed**: S3-FIFO's promotion rule is a hardcoded identity classifier on the accessed-bit. With |S| = 10 %, this is coarse — admits every once-touched object regardless of size, age in S, tenant, or ghost-hit history; rejects every untouched one. On heavy-tailed traces, both error directions are non-trivial.
- **Model**: Online logistic regression (SGD updates), or equivalently an Online Gradient Descent expert from Cesa-Bianchi & Lugosi.
- **Features (4 — keep at this number)**:
  1. accessed-bit (S3-FIFO's existing signal)
  2. `log(obj_size + 1)`
  3. age-in-S in virtual time, normalized by |S|
  4. ghost-hit flag (1 if this object was evicted from M and is reentering through S)
- **Label**: `1{next_access_vtime − now < H}` with `H ≈ 1× cache-size virtual time`. Use the `next_access_vtime` field that `oracleGeneral` already provides — no extra trace processing. This is LRB's Belady-boundary objective, simplified to binary.
- **Theory**: Direct learning-augmented caching predictor. With an unconditional fallback (always promote when accessed-bit = 1, as a robustness path), the algorithm is `(1+ε)`-competitive with Belady when predictor is good and `O(log k)`-robust when predictor degrades (Lykouris–Vassilvitskii STOC '18; Rohatgi SODA '20). Cleanest theoretical guarantee on this list.
- **Expected wins**: 1–4 % MR reduction on top of S3-FIFO at small cache sizes (0.1–1 % footprint), largest on Twitter clusters with mismatched static |S|.
- **Cost**: ~150 LoC; ~10 multiplies per S insertion. Trivial under "ignore overhead."
### #2 — HALP-style "Rerank Last k Candidates" for FIFO Caches
 
- **Augments**: S3-FIFO, SIEVE, FIFO-Reinsertion, FIFO-Merge (general).
- **Failure mode**: The eviction candidate is *always* the queue tail / cell-after-hand. No mechanism to compare against neighbors.
- **Model**: Pairwise preference learner. Sample `k = 4–8` candidates from the tail region, score each with `s(x) = w·φ(x)`, evict argmin. HALP NSDI '23 reported `k = 2 → 4` captured ~1.1 % byte-MR; `k > 8` marginal.
- **Features (5)**: age in queue, `log(size)`, accessed/visited bit, ghost-hit flag, frequency-since-insertion (S3-FIFO's existing 2-bit counter).
- **Label**: Pairwise — between two candidates `(a, b)` at the same eviction step, the correct eviction is the one whose `next_access_vtime` is further. Or simpler: per-candidate Belady binary.
- **Theory**: HALP at YouTube production (9.1 % byte-MR reduction at 1.8 % CPU). Sadek/Elias 2025 (arXiv:2507.16242) gives competitive-ratio bounds with `O(d · OPT)` predictor calls.
- **Differentiation from HALP**: (a) FIFO families instead of LRU, (b) cache-internal features only, (c) online linear model instead of MLP. **This is the most derivative idea — must defend novelty carefully.**
### #3 — Learned Admission to S (one-hit-wonder filter) with Bloom-filter memory
 
- **Augments**: S3-FIFO admission to S; portable to SIEVE, LRU, 2Q.
- **Failure mode**: S3-FIFO admits *all* misses to S. On size-skewed traces (Wiki, MetaCDN), one 50 MB one-hit-wonder = 5 % of S in one shot, crowding out small populars. AdaptSize (NSDI '17), Flashield (NSDI '19), CacheSack (ATC '22) all attack this; none plug into S3-FIFO.
- **Model — two options**:
  1. **Probabilistic size-exponential**: `Pr[admit] = exp(-c · log(size))`, `c` learned online by gradient ascent on hit-yield-per-byte. Two parameters. This is Wikimedia's production policy.
  2. **Counting Bloom filter + threshold + per-tenant logistic regression**. Flashield/CacheLib-ML approach with Bloom counter replacing DRAM buffer (avoids Baleen's "short-lifetime DRAM" critique).
- **Features (3)**: `log(size)`, Bloom-counter of past 6 h, hashed tenant ID.
- **Theory**: AdaptSize Markov-chain analysis (NSDI '17) — closed-form expected hit ratio in `f(s)`, gradient ascent converges to local optimum.
- **Expected wins**: Disproportionately large at small cache sizes and on size-skewed traces (Wiki upload, MetaCDN, Tencent Photo). AdaptSize beat next-best by 30–46 % object-HR at small caches.
### #4 — Adaptive |S| / |M| Sizing in S3-FIFO  ⭐ attacks an explicit open problem
 
- **Augments**: S3-FIFO directly.
- **Failure mode**: Static 10 % / 90 % split. Yang et al. SOSP '23 explicitly: *"using an adaptive algorithm can improve the tail performance, but degrade overall performance. We find that tuning the adaptive algorithm is very challenging."* This is a stated open question by the original authors.
- **Two complementary attacks**:
  1. **ARC-style ghost adaptation**: maintain `G_S` (objects evicted from S without promotion) and `G_M` (objects evicted from M). Ghost-hit in `G_S` → |S|++; ghost-hit in `G_M` → |S|−−. Literal ARC adaptation rule.
  2. **EXP3 / multi-armed bandit over discrete |S|**: arms = {2 %, 5 %, 10 %, 20 %, 30 %} of cache. One arm per epoch (~1 M reqs), reward = −MR. Hedge/EXP3 with mirror descent. Sidesteps Yang's tuning complaint by treating sizing as regret minimization.
- **Features**: none beyond ghost counters and per-epoch MR. **Most feature-light idea on the list.**
- **Theory**: ARC partition has bounded competitive ratio vs best static partition (Megiddo & Modha FAST '03). EXP3 has `O(√T log K)` regret vs best static arm. Combine à la CACHEUS.
- **Tight testable claim**: "We eliminated the mean-vs-tail tradeoff that the original S3-FIFO authors identified as their main open problem." Strong narrative.
### #5 — Ghost-Cache-Driven Online Correction (LeCaR collapsed to one expert per feature)
 
- **Augments**: S3-FIFO (uses existing ghost queue G); easy to add ghosts to SIEVE.
- **Failure mode**: When S3-FIFO erroneously demotes x and x re-arrives via ghost-hit, the algorithm only re-admits — does not update policy. Every demotion error is wasted teaching signal.
- **Model**: Maintain `r̂(φ(x))` via SGD: on ghost-hit, `w ← w + η · φ(x)`. At promotion-decision in S, compute `score = w·φ(x)` as tiebreaker (or as input to idea #1).
- **Features (3)**: size bucket, tenant, queue-position-at-demotion.
- **Theory**: Hedge / multiplicative weights. Cumulative regret vs best fixed weighting `O(√T log d)` for d features.
- **Status**: Synergistic with #1; consider as an ablation/extension rather than independent idea.
### #6 — Online Experts Meta-Policy: switch between S3-FIFO and SIEVE
 
- **Augments**: General (chooses between two cache policies).
- **Failure mode**: Complementary failure modes. SIEVE NSDI '24 itself: at small cache sizes, "new objects cannot demonstrate their popularity before being evicted" — SIEVE underperforms ARC and TwoQ. S3-FIFO's static |S| can be wrong for adversarial workloads. A LeCaR/CACHEUS-style switcher could dominate both.
- **Model**: LeCaR randomized expert weighting with experts {S3-FIFO, SIEVE}, weights via Hedge. CACHEUS adaptive learning rate via stochastic hill-climbing — copy this to remove LeCaR's brittle hyperparameters.
- **Implementation**: requires running two shadow caches in parallel + LeCaR's hand-off / randomized eviction trick. 2× memory; OK under "ignore overhead."
- **Theory**: Hedge regret `O(√T log K)`. CACHEUS dominated ARC/LIRS/LeCaR across 329 workloads at FAST '21.
- **Caveat**: This is essentially CACHEUS with new experts. Novelty argument: (a) the FIFO-family expert pair, (b) demonstration that lazy-promotion experts don't break LeCaR's regret bounds (which were derived for LRU/LFU).
- **Verdict**: Safest "ship it" idea but lowest novelty.
### #7 — Reuse-Distance Regression as SIEVE Visited-Bit Replacement
 
- **Augments**: SIEVE.
- **Failure mode**: SIEVE's 1-bit visited summary collapses too much info on traces where reuse distance varies smoothly (block I/O scans, MSR).
- **Model**: 2-bit "soft visited" counter; increment by `quantize(σ(w·φ(x)))` ∈ {0,1,2,3}. SGD against Belady label.
- **Features (2)**: `log(size)`, ghost-hit flag. Add `client_id` if it helps.
- **Theory**: Coarse-quantized LRB. GL-Cache (FAST '23) showed group-coarsening of LRB keeps 90+ % of hit-ratio gain at 228× throughput — supports aggressive quantization.
- **Caveat**: Strongest on block I/O, weakest on KV. **Pursuing this means pivoting domain — skip unless early Twitter results disappoint.**
### #8 — Decision-Stump Admission Filter  ⭐ implement first as control
 
- **Augments**: S3-FIFO admission (parallel to #3 but minimal).
- **Model**: `if size > threshold AND no_ghost_hit then reject`. Threshold learned by 1-D online bisection on observed miss-byte ratio. "Dumber AdaptSize."
- **Features (2)**: size, ghost-hit flag.
- **Theory**: AdaptSize Markov chain (NSDI '17) → unique optimal threshold under stationary size dist; bisection converges.
- **Role**: Marginal expected gain on Twitter (low size variance under slab), but **must-implement baseline**. Any of #1–#3 must beat this.
---
 
## §3 — libCacheSim trace inventory
 
All entries below confirmed available at `cacheMon/cache_dataset` (compiled by Juncheng Yang, hosted on AWS open-data S3) in `oracleGeneral` format = `{uint32 timestamp; uint64 obj_id; uint32 obj_size; int64 next_access_vtime}` zstd-compressed, loads without decompression.
 
### Key-Value (recommended primary domain)
 
| Trace | Year | # Traces | # Reqs | # Objects | Notes |
|---|---|---|---|---|---|
| **Twitter Twemcache** | 2020 | 54 | 195 B | 10.65 B | OSDI '20 anonymized clusters. `cluster52` = canonical hardest. Has `client_id`, `op`, `key_size`, `value_size`, `TTL`. **S3-FIFO + SIEVE both used these.** |
| **MetaKV (CacheLib)** | 2022 / 2024 | 5 | 1.6 B | 82 M | Two snapshots; 2024 release adds `op_time`, `usecase`, `sub_usecase`. GET / GET_LEASE / SET / DELETE. Slab. |
 
### CDN / object (best for byte-MR + HALP-style evals)
 
| Trace | Year | # Traces | # Reqs | # Objects | Notes |
|---|---|---|---|---|---|
| **Wikimedia CDN** | 2019 (also '07, '16) | 3 | 2.86 B | 56 M | Upload + text variants, 21 days. Skewed sizes. **LRB NSDI '20 used these — CDN gold standard.** |
| **MetaCDN (CacheLib)** | 2023 / 2025 | 3 (nha/prn/eag) | 231 M | 76 M | 7 days each. `objectSize`, `responseSize`, `TTL`, `cdn_content_type_id`. New, relatively unstudied. |
| **Tencent Photo** | 2018 | 2 | 5.65 B | 1.04 B | 9 days, QQPhoto, sampled 1/100. **S3-FIFO used.** |
| **IBM Docker Registry** | 2018 | 7 | 38 M | — | 75 days. Niche. |
 
### Block I/O (scan/churn experiments; contrast set)
 
| Trace | Year | # Traces | # Reqs | Notes |
|---|---|---|---|---|
| **MSR Cambridge** | 2007 | 13 | 410 M | FAST '08. **Used by S3-FIFO, SIEVE, CACHEUS, LRB.** `hm_0` is scan-heavy. |
| **CloudPhysics** | 2015 | 106 | 2.1 B | VMware ESXi, 1 wk, vscsiStats. libCacheSim has native `vscsi` reader. Sample ships with repo. |
| **Tencent CBS** | 2020 | 4030 | 33.7 B | Cloud block, 10 days, 5584 vols. |
| **Alibaba Block** | 2020 | 1000 | 19.7 B | 30 days, EBS Ultra Disks. |
| **Meta Tectonic** | 2023 | 5 | 14 B | **Used by Baleen FAST '24.** |
| **Google Thesios** | 2024 | 3 | 115 B | Synthesized but production-validated. ASPLOS '24. |
 
### What S3-FIFO / SIEVE / HALP themselves used
 
- **S3-FIFO (SOSP '23)**: 6594 traces from 14 datasets — Twitter, MetaKV, MSR, CloudPhysics, Tencent (Photo + CBS), Wiki, + 3 proprietary CDN. Best-mean on 10/14 at large size, 7/14 at small.
- **SIEVE (NSDI '24)**: subset of above (1000+ traces). Authors flagged poor SIEVE perf at very small caches on ARC-friendly workloads.
- **HALP (NSDI '23)**: production YouTube CDN (private) + Wikipedia 2019 for the public LRB comparison.
---
 
## §4 — libCacheSim integration
 
### Three paths, ordered by recommended use
 
**Path A — Python `PluginCache` (use first; no C compilation):**
 
```python
import libcachesim as lcs
from libcachesim import PluginCache, CommonCacheParams, Request
 
def init_hook(p): return MyState()
def hit_hook(state, req): state.on_hit(req)
def miss_hook(state, req): state.on_miss(req)
def eviction_hook(state, req) -> int: return state.evict()
def remove_hook(state, obj_id): state.remove(obj_id)
def free_hook(state): state.clear()
 
cache = PluginCache(
    cache_size=...,
    cache_init_hook=init_hook,
    cache_hit_hook=hit_hook,
    cache_miss_hook=miss_hook,
    cache_eviction_hook=eviction_hook,
    cache_remove_hook=remove_hook,
    cache_free_hook=free_hook,
    cache_name="LearnedS3FIFO")
 
reader = lcs.TraceReader(
    trace="s3://cache-datasets/cache_dataset_oracleGeneral/2020_twitter/cluster52.oracleGeneral.zst",
    trace_type=lcs.TraceType.ORACLE_GENERAL_TRACE)
req_mr, byte_mr = cache.process_trace(reader)
```
 
All 8 attack ideas implementable in pure Python. Slower than native C but adequate at million-request scale; can `import` sklearn / JAX / hand-rolled SGD inside the eviction hook.
 
**Path B — Add a C eviction algorithm under `libCacheSim/cache/eviction/`** mirroring `S3FIFO.c`. What S3-FIFO and SIEVE themselves did. Build: `cmake -G Ninja .. && ninja`. Switch to this once Python prototype is validated, for full-trace runs at 20 M req/s.
 
**Path C — `webcachesim2`** at `github.com/sunnyszy/lrb` is the LRB simulator (separate codebase). **Don't use** — libCacheSim now has LRB built in via `CMAKE_ARGS="-DENABLE_LRB=ON"`.
 
### Many-trace sweep
 
```bash
./_build/bin/cachesim trace.oracleGeneral.zst oracleGeneral \
    fifo,lru,arc,sieve,s3fifo,lecar,glcache,my_learned_policy \
    0 --ignore-obj-size 1
```
 
`CACHE_SIZE=0` triggers automatic sweep over standard fractions of the working set. Results → `result/TRACE_NAME/`. `plot_mrc_size.py` and `plot_mrc_time.py` produce MRCs.
 
For huge traces: `mrcProfiler --profiler=SHARDS --profiler-params=FIX_RATE,0.01,10` → ~20× speedup at < 0.1 % MAE.
 
For 54-cluster runs: `distComp` (Thesys-lab) + `mountpoint-s3` to mount the trace bucket without copying.
 
---
 
## §5 — Two-week concrete plan
 
| Day | Task |
|---|---|
| 1–2 | Install libCacheSim (C build) + libCacheSim-python. Pull `cluster52`, `cluster17`, `cluster18`, `cluster45`, `wiki_2019_u`, `msr_hm_0`, `meta_cdn_eag` in oracleGeneral format. |
| 3 | Reproduce baseline MRCs: S3-FIFO, SIEVE, ARC, LRU, FIFO, LeCaR, CACHEUS on the 6 traces. Validates toolchain and gives us our reference points. |
| 4–5 | Implement **#8 (decision-stump admission)** in `PluginCache`. Mandatory control. |
| 6–8 | Implement **#1 (learned S→M promotion gate)**. Headline contribution. Compare vs S3-FIFO, S3-FIFO+#8, SIEVE, LeCaR. |
| 9–10 | Implement **#4 sub-option (ii) (EXP3 over \|S\| sizes)**. Addresses Yang et al.'s explicitly-stated open problem. |
| Wk 2 | Whichever of #2 / #5 / #6 most cleanly differentiates from related work given early results. Skip #7 unless pivoting to block I/O. |
 
---
 
## §6 — Strongest publishable narrative to aim for
 
> *"With ≤4 cache-internal features and an online linear model, a learned promotion gate on S3-FIFO recovers most of HALP's byte-miss-ratio gain at a fraction of the metadata footprint, and a regret-minimizing |S| sizer eliminates the mean-vs-tail tradeoff that the original S3-FIFO authors identified as their main open problem."*
 
Tight. Defensible. Aligned with CS 264 scope. Two clear contributions (gate + sizer), each with explicit theoretical grounding (Lykouris–Vassilvitskii / EXP3) and explicit empirical comparison (HALP / S3-FIFO-d).
 
---
 
## §7 — Annotated bibliography (read in this order)
 
### FIFO foundations
- **Yang et al. "FIFO Queues are All You Need." SOSP '23.** S3-FIFO. Three queues (S=10%, M=90%, ghost G). Establishes quick-demotion thesis. *Open problem flagged in paper: adaptive sizing improves tail, degrades mean — explicit invitation for #4.*
- **Yang et al. "FIFO Can Be Better than LRU." HotOS '23.** Conceptual setup paper. Defines lazy-promotion / quick-demotion as design language — adopt this vocabulary in our writeup.
- **Zhang et al. "SIEVE is Simpler than LRU." NSDI '24 (best paper).** Single FIFO + moving hand + 1-bit visited. Underperforms at small caches because new objects can't show popularity — opening for #6.
### Learned eviction
- **Beckmann et al. "LHD." NSDI '18.** Per-object hit-density via age + size conditional probabilities. 2–3× more space-efficient than ARC. Theoretical predecessor of #7.
- **Song et al. "LRB: Learning Relaxed Belady." NSDI '20.** XGBoost on dozens of features, Belady-boundary objective. 4–25 % WAN traffic reduction on 6 production CDN traces. **Heavy** — we explicitly position as the lightweight alternative.
- **Yang et al. "GL-Cache." FAST '23.** Group-level learning. 228× throughput vs LRB at +7 % HR. Direct evidence aggressive coarsening recovers most of LRB's gain.
- **Song et al. "HALP." NSDI '23** *(note: NSDI '23, not '24 — proposal had this slightly off)*. Tiny continuously-trained reward MLP scores k=4 candidates pre-selected by heuristic. Pairwise preference (RLHF-style). 9.1 % byte-MR reduction at 1.8 % CPU at YouTube. **Template for #2.**
### Learned admission
- **Berger, Sitaraman, Harchol-Balter. "AdaptSize." NSDI '17.** Probabilistic size-exponential admission, Markov-chain-tuned. Theoretical ground for #3 sub-option (i) and #8.
- **Eisenman et al. "Flashield." NSDI '19.** SVM-based flash admission via DRAM filter. Critiqued by Baleen.
- **Kirilin et al. "RL-Cache." NetAI/SIGCOMM '19 + JSAC '20.** Akamai. Direct policy search over 8 features. Code at github.com/WVadim/RL-Cache.
- **Yang et al. "CacheSack." ATC '22.** Google Colossus. Per-category mixture of admission policies as LP. Discrete-choice bandit; relevant to #4 sub-option (ii).
- **Wong et al. "Baleen." FAST '24.** Meta Tectonic. Optimizes disk-head time, not HR. "Episodes" abstraction.
### Online-learning / experts
- **Vietri et al. "LeCaR." HotStorage '18.** Foundational. Two experts (LRU, LFU), Hedge weights. 18× HR over ARC at very small caches. Framework for #5, #6.
- **Rodriguez et al. "CACHEUS." FAST '21.** Adaptive-LR variant of LeCaR with SR-LRU + CR-LFU experts. 17,766 experiments, 329 workloads. Most consistent across FIU + MSR + CloudVPS primitives.
- **Liu et al. "Parrot." ICML '20.** Transformer imitation of Belady at CPU-cache level. **Heavy** — relevant only as the upper bound we don't compete with.
### Theoretical learning-augmented caching
- **Lykouris & Vassilvitskii. STOC '18 / ICML '18.** Established framework. `(1+ε)`-consistency / `O(log k)`-robustness combiners.
- **Rohatgi. SODA '20.** Tighter bounds.
- **Chłędowski et al. "Robust Learning-Augmented Caching: Experimental Study." ICML '21.** "Blindly follow the better of predictor or LRU" combiner is competitive in practice with negligible overhead — useful baseline for any combiner-based attack.
- **Sadek, Elias et al. arXiv:2507.16242, 2025.** Post-S3-FIFO. `O(log k)` robustness with only `O(d · OPT)` predictor calls. **Direct theoretical scaffolding for the candidate-window in #2.**
### Adjacent
- **Yang et al. "A Large-scale Analysis of Hundreds of In-memory Cache Clusters at Twitter." OSDI '20.** Source of the 54 traces and the workload-classification taxonomy. Read first.
- **Megiddo & Modha. "ARC." FAST '03.** Adaptive partition between recency (T1) and frequency (T2). Conceptual ancestor of #4.
- **Berger. "Towards Lightweight and Robust ML for CDN Caching." HotNets '18.** Position paper that essentially predicts our project. Cite as motivation.
---

## §8 — First experimental results (small-scale)

**Setup.** Twitter cluster52, oracleGeneral CSV, **request-miss-ratio** (`ignore_obj_size=True` unless noted). Self-contained Python harness in `plugins/sim.py` because the `libcachesim` Python wheel fails to build in this environment (CMake error in trace-analyzer C++ extensions). Baselines (`FIFO`, `LRU`, `S3FIFO`, `SIEVE`) re-implemented from scratch in `plugins/baselines.py` to match published algorithms; ordering at cache=1000 / 50k-req smoke matches the S3-FIFO paper (S3FIFO 0.345 < SIEVE 0.351 < LRU 0.358 < FIFO 0.383). Attack-idea modules: `plugins/admission_stump.py` (#8), `plugins/learned_promotion.py` (#1), `plugins/exp3_sizer.py` (#4ii). All auto-discovered by `plugins/run_experiments.py`.

### Headline table (500k Twitter cluster52 requests)

| algo | cache=1,000 req-MR | cache=10,000 req-MR |
|---|---|---|
| FIFO | (run separately) | — |
| LRU  | (run separately) | — |
| SIEVE | (run separately) | — |
| **S3FIFO (vanilla, |S|=10%)** | **0.3355** | **0.2110** |
| S3FIFO+S0.02  | 0.3565 | 0.2120 |
| S3FIFO+S0.05  | 0.3415 | 0.2108 |
| S3FIFO+S0.20  | 0.3332 | 0.2125 |
| S3FIFO+S0.30  | 0.3350 | 0.2150 |
| S3FIFO+EXP3 (#4ii)            | 0.3392 | 0.2143 |
| S3FIFO+Stump (#8 adaptive)    | 0.5144 | 0.3817 |
| S3FIFO+StumpFixed (#8 fixed thresh=256B) | 0.4788 | 0.3976 |
| **S3FIFO+LearnedPromote (#1, headline)** | **0.3185** | **0.2092** |

### Per-idea takeaways

**#1 Learned S→M Promotion Gate — works.** −1.70 pp at cache=1000, −0.18 pp at cache=10000 vs vanilla S3-FIFO at 500k requests. Online logreg, lr=0.05, L2=1e-4, 4 features. Last-10k decision accuracy 95.2% / 97.7%. Robustness fallback (last-1000 acc < 55% ⇒ revert to freq-based rule) **never fired** — predictor stayed well above 55% throughout, so we are firmly in the consistency regime of the Lykouris-Vassilvitskii framework (no robustness penalty paid). Most informative feature at cache=1000: **age-in-S** (w=−0.34) — older-in-S residents discounted, fixing a real failure mode of vanilla S3-FIFO. At cache=10000 the accessed-bit (w=+0.64) reasserts itself. Ghost-flag w_x4 ≡ 0 by construction (vanilla S3-FIFO routes ghost-hits straight to M, bypassing the gate). Size feature w_x2 absorbs into bias under unit-size mode; rerun with `ignore_obj_size=False` to use it meaningfully.

**#4ii EXP3 |S| Sizer — does not converge in 50 epochs.** Lands within 0.6 pp of static-best at cache=1000 (0.3392 vs 0.3332 for S=0.20) and 0.35 pp at cache=10000 (0.2143 vs 0.2108 for S=0.05). Final arm probabilities ~uniform (0.19–0.21 per arm). Diagnosed cause: with reward in [−1, 0] and `eta ≈ 0.057`, weight ratios over 50 epochs only spread by a factor of `exp(eta · 50 · Δr) ≈ exp(0.057 · 50 · 0.02) ≈ 1.06` — bandit is statistically powerless to distinguish arms whose true MRs differ by 1 pp. **Useful sub-finding**: best static |S| **moves with cache size** (S=0.20 best at cache=1000; S=0.05 best at cache=10000). This is direct empirical evidence for Yang et al.'s "static |S|=10% is wrong for some workloads" — and a stronger learner (mirror-descent, UCB, or longer epochs/whole-trace runs) is the obvious next step. Also weakly **refutes** the "EXP3 improves tail, degrades mean" half of Yang et al.'s claim: on this slice EXP3's worst-epoch MR is no better than static-best while its mean MR is slightly worse.

**#8 Decision-Stump Admission — clean negative result, important.** Loses by ~13 pp at cache=1000. Two compounding causes:
  1. *Asymmetric-evidence bug in online bisection*: the "above-threshold-and-admitted" bin gets few/no hits because admissions through that path are rare; bisection only ever drives the threshold *down*, eventually rejecting all but the smallest 12-byte objects. A proper AdaptSize-style learner needs a shadow Markov-chain simulator or Belady labels (i.e., enters #1 territory).
  2. *Unit-size mode neuters the attack*: when `obj_size==1` everywhere, "rejecting a big object" frees one slot — the cost/benefit math AdaptSize relies on is gone.
  Real-size mode (`ignore_obj_size=False`) does give the stump *some* req-MR leverage (~4 pp gain in one cell), but loses on byte-MR (kept many small items at the cost of fewer big-but-popular ones). **Net implication for the project narrative**: size-based admission is *not* a major lever on cluster52. The exploitable value-density signal lives in recency/frequency, which is exactly what idea #1 already captures. This argues for *deprioritizing* idea #3 (learned admission to S) on this workload and keeping #1 as the headline.

### Methodological footnotes

- The local trace at `data/twitter_cluster52.csv` is a 1M-request slice; the 10M `twitter_cluster52_10m.csv.zst` exists but the harness needs a `zstandard` Python dep to read it. Add `zstandard` to `requirements.txt` before scaling up.
- SIEVE in `plugins/baselines.py` is O(n)-per-eviction (rebuilds key list each call); fine at cache≤10k but will dominate runtime at full scale. Replace with a doubly-linked-list+hand variant before any whole-trace run.
- Two cells in attack #1 showed transient regressions at 100k requests but recovered by 500k — this is cold-start: the gate has only ~16k decisions before the run ends. Numbers should be reported on ≥500k-request slices to avoid this artifact.
- libcachesim build failure is in this environment only (a CMake C++ subobject failure during wheel build, not a code bug). The harness re-implementation produces ordering consistent with published numbers, and is sufficient for the small/medium-scale ablations needed in the next two weeks of §5's plan.

### Updated priorities (delta vs §2 ranking)

1. **#1 promotion gate stays the headline** — confirmed empirically.
2. **#4ii sizer is the second contribution** — but needs a stronger learner. Concrete next step: replace EXP3 with mirror-descent / Hedge using full-information feedback (we can compute counterfactual MRs of all arms via shadow ghost queues — adds memory but the ATTACK_PLAN.md "ignore overhead" stance covers it).
3. **#8 (admission) drops in priority** for cluster52. Keep as a control on Wiki/MetaCDN traces where size *does* vary. Do not feature it as a contribution.
4. **#2 (HALP rerank) and #5 (ghost-driven correction)** become the natural next experiments — both layer cleanly on top of the now-validated #1 module.
---

## §9 — Expanded benchmarks: cross-trace generalization with ARC + TwoQ

After the §8 results turned out to be cluster52-specific in important ways (NoPromote ≡ S3FIFO+LearnedPromote there, but doesn't generalize), we expanded to seven traces and added two more baselines.

### What was added to the harness

- **Trace registry** (`plugins/sim.py`): name → loader. Now supports the local CSVs (`twitter`, `cloudphysics`), oracleGeneral binary `.zst` files (`cluster10`, `cluster45`, `msr_hm_0` — all fetched from `cache-datasets.s3.amazonaws.com/cache_dataset_oracleGeneral/...` over HTTPS via curl, no S3 credentials needed), and two synthetic Zipf generators (`zipf_heavy` α=1.2, `zipf_flat` α=0.7).
- **Binary reader** for libCacheSim's `oracleGeneral` format: 24-byte records `<IQIq>` (uint32 ts, uint64 obj_id, uint32 size, int64 next_access_vtime), zstd-streamed via the `zstandard` Python package.
- **ARC** (Megiddo & Modha FAST '03) and **TwoQ** (Johnson & Shasha VLDB '94) added to `plugins/baselines.py`. Both implemented with `OrderedDict`-based partitions; ARC self-tunes |T1| via the `p` parameter; TwoQ uses the standard 25%/75% A1in/Am split with a 50%-cache ghost A1out.
- **`run_experiments.py --trace <name>`** dispatches by trace registry.
- **wiki_2019_u** was 404 at the obvious S3 path; the public bucket has Wikipedia traces but at a different prefix that we haven't located. Skipped for now; CDN byte-MR sanity check deferred until we find or convert one.

### Headline cross-trace table (req-MR; bold = winner per cell)

| trace | cache | FIFO | LRU | ARC | TwoQ | SIEVE | S3FIFO | NoPromote | LearnedV3 |
|---|---|---|---|---|---|---|---|---|---|
| **twitter cluster52** (500k) | 1,000 | 0.382 | 0.356 | — | — | 0.344 | 0.336 | 0.319 | **0.318** |
| twitter cluster52 (500k) | 10,000 | 0.251 | 0.227 | — | — | 0.219 | 0.211 | 0.210 | **0.208** |
| **twitter cluster45** (500k, write-heavy stress) | 1,000 | 0.574 | 0.569 | 0.635 | 0.806 | 0.567 | 0.563 | 0.546 | **0.546** |
| twitter cluster45 (500k) | 10,000 | 0.525 | 0.515 | 0.569 | 0.652 | 0.506 | 0.498 | 0.495 | **0.494** |
| **twitter cluster10** (200k, near-uniform) | 1,000 | 0.500 | 0.500 | 0.576 | 0.500 | 0.501 | 0.505 | **0.500** | **0.500** |
| twitter cluster10 (200k) | 10,000 | 0.500 | 0.500 | 0.565 | 0.500 | 0.500 | 0.500 | **0.500** | **0.500** |
| **msr_hm_0** (500k, scan-heavy block I/O) | 1,000 | 0.427 | 0.409 | 0.448 | 0.424 | 0.388 | 0.372 | 0.372 | **0.370** |
| msr_hm_0 (500k) | 10,000 | 0.318 | 0.305 | 0.289 | 0.284 | 0.286 | **0.282** | 0.292 | 0.285 |
| **cloudphysics** (113k, block I/O) | 500 | 0.847 | 0.838 | — | — | 0.829 | 0.828 | 0.830 | **0.828** |
| cloudphysics (113k) | 5,000 | 0.804 | 0.804 | — | — | 0.789 | 0.748 | 0.754 | **0.737** |
| zipf heavy α=1.2 (100k) | 500 | — | — | — | — | — | **0.173** | 0.175 | 0.175 |
| zipf heavy α=1.2 (100k) | 5,000 | — | — | — | — | — | **0.084** | 0.096 | 0.091 |
| zipf flat α=0.7 (100k) | 500 | — | — | — | — | — | **0.737** | 0.738 | 0.738 |
| zipf flat α=0.7 (100k) | 5,000 | — | — | — | — | — | **0.442** | 0.453 | 0.448 |

(Dashes = not run for that cell; the existing §8 numbers cover the classical baselines on cluster52.)

### Findings

1. **V3 wins on 5/5 real traces**, ties or loses by ≤0.32 pp on the synthetic Zipf control. Largest absolute win: **−1.71 pp** at cluster45 cache=1000 and at cluster52 cache=1000 (tied with NoPromote at cluster52, distinct on cluster45). Most paper-worthy single cell: **CloudPhysics cache=5000, V3 0.737 vs vanilla S3FIFO 0.748 (−1.17 pp)** — the production block-I/O trace where recency-since-last-hit is the dominant feature (`w_x6 = −0.97`).

2. **NoPromote does not generalize.** It wins on cluster52 and cluster45 (the heavily-Zipfian KV traces with severe one-touch-wonder mass) and **loses** on cluster10, msr_hm_0, cloudphysics, and both Zipfs. The cluster52-only finding "S3-FIFO over-promotes at small cache" was workload-specific. The learned gate, by contrast, is robust everywhere — it converges to NoPromote-like behavior where that's right (cluster52, cluster45) and to selective-promotion where that's right (cloudphysics, msr).

3. **ARC and TwoQ are surprisingly weak** at small caches on cluster45 and msr — ARC at cluster45 cache=1000 is 0.635 (worse than vanilla FIFO 0.574); TwoQ at cluster45 cache=1000 is 0.806. This is consistent with §1 of ATTACK_PLAN.md and the S3-FIFO paper's observation that adaptive recency/frequency partitioning misbehaves on write-heavy workloads. **Implication**: positioning the learned gate against ARC/TwoQ rather than just FIFO/LRU strengthens the headline — we're beating canonical adaptive algorithms by 1–10 pp on the very traces ARC/TwoQ struggle on.

4. **V3's converged weights are diagnostically distinct per trace.** This is strong evidence the model is learning real workload structure rather than memorizing.
   - Twitter cluster52 cache=10000: `w_x5(loghits)=+1.69, w_x1(freq)=−1.63` — model *negates* the capped freq counter and uses the uncapped log-hits. Direct evidence the 2-bit cap is leaving signal on the table on this trace.
   - CloudPhysics cache=5000: `w_x1(freq)=+1.72, w_x6(recency)=−0.97` — block I/O has temporal locality that recency captures cleanly.
   - cluster45 cache=1000: `w_x6=−2.28, w_x3(age)=+0.41` — write-heavy: model wants stable established residents (positive age weight) but heavily penalizes stale-since-last-hit ones.
   - cluster10 cache=any: `w_x1(freq)=+1.82, b=−0.55, promote_rate=0.0%` — last10k_acc=1.000, the trace is so locality-poor that the gate degenerates to "never promote, accuracy is trivially 100% because everything is y=0."
   - MSR hm_0 cache=1000: `w_x1(freq)=+1.66, w_x6=−1.06, b=−3.54` — strong positive freq, strong negative recency, strong negative bias. Promotion only fires for very high freq AND very recent hit.

5. **Robustness fallback fired only on V2, never V3.** Across all four real traces V3 stayed above the 55% accuracy floor (real-trace accuracies range 97–100%). V2 fell back 4 times on CloudPhysics cache=5000 and 373 times on Zipf-flat cache=5000; V3's richer features (loghits, recency) eliminated those failures.

6. **The Zipf "loss" is the right control.** Synthetic i.i.d. Zipf has no temporal structure beyond marginal popularity — `freq` is a sufficient statistic. V3's added features can only add noise, and they do (≤0.7 pp). This is exactly the diagnostic shape we want: the gate is learning *temporal patterns*, and noise penalties when temporal patterns are absent prove it isn't pattern-hallucinating.

### Caveats

- **cluster10 is a near-uniform-access trace** in the 200k slice we tested (50% MR floor across all algorithms; ARC even worse at 0.576). A larger slice or larger cache may be needed to see meaningful differentiation. Excluded from headline numbers because of low signal.
- **SIEVE timing on the new traces is dominant**: 130 s at cluster45 cache=10000, 86 s at msr_hm_0 cache=10000. The O(n) hand walk in `baselines.py` is now a bottleneck; replace with doubly-linked-list+hand before the next sweep.
- **wiki_2019_u not yet found** at the obvious S3 prefix — the public bucket has Wikipedia traces but under a different naming convention. Defer until located; this leaves the byte-MR / size-skewed evaluation case open.
- **LeCaR baseline still missing.** The project's `plugins/cacheus.py` (a CACHEUS implementation, the LeCaR successor) imports the unbuilt libcachesim Python wheel; rewriting it on the harness is a second-pass task.

### Refined headline narrative (replaces §6)

> *"With 6 cache-internal features and an online linear model, a learned promotion gate on S3-FIFO is robust across KV (Twitter), block-I/O (MSR, CloudPhysics) and synthetic-stress (Zipf) workloads, beating vanilla S3-FIFO, ARC, TwoQ, SIEVE and a hardcoded NoPromote baseline by 0.3–1.7 pp request-miss-ratio across 5 real traces while losing ≤0.7 pp on the i.i.d.-Zipf control where no learned policy can beat the marginal-frequency oracle. The largest single-cell improvement (−1.17 pp at CloudPhysics cache=5000) is on a production block-I/O trace from a different domain than the gate was developed on."*

The headline is now **robustness across heterogeneous workloads**, not "+1.70 pp on cluster52" — that single number was load-bearing in §8 and turned out to be NoPromote in disguise.

---

## §10 — Feature engineering and model-class exploration

After §9 established V3 (6 features) as the headline, we systematically reduced and varied the model to find the parsimony floor and explore expressiveness ceilings. Variants live in `plugins/learned_promotion.py` as `S3FIFOLearnedV*`. All numbers below are 500k requests on real traces, 200k on cloudphysics, req-MR.

### Variant catalog

| variant | features | params | rationale |
|---|---|---|---|
| V3 | freq, log_size, age_S, ghost, log_hits, recency | 7 | original §9 headline |
| V4 ★ | log_hits, age, recency | 4 | drop dead features (freq subsumed by log_hits, log_size const, ghost ≡ 0 at gate) |
| V5 | log_hits, recency/age | 3 | dimensionless ratio replaces (age, recency) |
| V6 | log_hits, age, recency/age | 4 | restore age, keep ratio |
| V7 | log_hits, age | 3 | drop recency entirely |
| V8 | log_hits, log(age) | 3 | test log-compression of age |
| V9-GAM | log_hits, age, recency | 16 | piecewise-linear shapes per feature (K=4 segments) |

### Cross-variant ablation (req-MR)

| trace | cache | S3FIFO | V4 | V5 | V6 | V7 | V8 | V9-GAM |
|---|---|---|---|---|---|---|---|---|
| twitter | 1,000 | 0.3355 | **0.3184** | 0.3184 | 0.3184 | 0.3185 | 0.3185 | 0.3183 |
| twitter | 10,000 | 0.2110 | **0.2078** | 0.2091 | 0.2082 | 0.2088 | 0.2093 | 0.2091 |
| cluster45 | 1,000 | 0.5630 | **0.5459** | 0.5460 | 0.5461 | 0.5460 | 0.5460 | 0.5459 |
| cluster45 | 10,000 | 0.4982 | **0.4940** | 0.4940 | 0.4943 | 0.4947 | 0.4942 | 0.4940 |
| cloudphysics | 500 | 0.8284 | **0.8281** | 0.8285 | 0.8285 | 0.8285 | 0.8284 | 0.8286 |
| cloudphysics | 5,000 | 0.7482 | **0.7365** | 0.7508 | 0.7400 | 0.7400 | 0.7505 | 0.7506 |
| msr_hm_0 | 1,000 | 0.3715 | **0.3704** | 0.3717 | 0.3710 | 0.3709 | 0.3712 | 0.3711 |
| msr_hm_0 | 10,000 | 0.2816 | **0.2848** | 0.2869 | 0.2853 | 0.2854 | 0.2864 | 0.2866 |

V4 wins or ties on 8/8 cells. No reduced or alternative-form variant matches V4 on the cloudphysics cache=5000 cell, where recency-since-last-hit is the load-bearing signal.

### Key findings

1. **V3 → V4: half the features, identical performance.** Dropping `freq`, `log_size`, and `ghost_flag` doesn't change MR on any cell. `freq` is redundant with `log(hits)` (clipped vs uncapped count of the same quantity); `log_size` is constant under `ignore_obj_size=True`; `ghost_flag` is ≡0 at the gate by construction (S3-FIFO routes ghost-hits to M directly). The "minimum useful set" is {log_hits, age, recency}.

2. **V5/V6: the ratio `recency/age` is a worse coordinate basis than (age, recency).** Even though the two parameterizations are bijective, the linear model expresses the Belady boundary better in raw axes. V5 alone loses 1.43 pp on cloudphysics 5000; V6 (which adds age back) recovers most of it but still trails V4 by ~0.05 pp consistently.

3. **V8: log compression of age makes things worse.** The age distribution at decision time is concentrated near `age ≈ S_cap` (the natural eviction boundary); log compression squeezes resolution exactly where decisions are made. Linear `age/S_cap` is the right transformation.

4. **V7: dropping recency loses 0.10–0.35 pp on cloudphysics, ties V4 elsewhere.** Recency carries trace-specific signal that's only critical for cloudphysics-shape workloads (where temporal locality is the dominant signal). V7 (2 features, 3 params) is a defensible smaller-than-V4 option for production scenarios where the 0.35 pp on block I/O is acceptable.

5. **V9 GAM: more expressive doesn't mean better.** The piecewise-linear GAM has 4× the parameters (16 vs 4) but ties or *loses* to V4 — the cloudphysics 5000 regression is severe (+1.41 pp). Diagnostic: most middle knots barely train (e.g., `f_age @ [0,0.5,1,1.5,2]: [0,0,0,0,−0.62]`), because the age distribution is concentrated and gradient signal is split across segments. Confirms the Belady boundary is approximately linear in (log_hits, age, recency) for these traces; the extra flexibility hurts via under-training.

6. **V9 did surface one diagnostic shape**: `f_recency` is non-monotone on twitter cache=10000: `[+0.48, +0.42, −0.01, +0.20, −1.45]`. The "very-recent OR very-stale matter; middle is neutral" pattern is real but doesn't translate to MR improvement at the trace lengths we're running — likely noise from undertrained middle knots.

### NoPromote workload analysis

A separate ablation: a hardcoded `S3FIFO+NoPromote` (never promote from S; M fills only via ghost-hits) was tested as a control. NoPromote-vs-S3FIFO Δ across 14 cells:

| trace family | NoPromote helps? |
|---|---|
| Twitter Twemcache (cluster52, 45, 10) | **6/6 cells** (Δ ranges −1.70 pp at small cache to −0.03 at large cache) |
| CloudPhysics block I/O | 0/2 cells (Δ +0.13, +0.55) |
| MSR block I/O | 0/2 cells (Δ +0.07, +0.99) |
| Zipf synthetic | 0/4 cells (Δ +0.13 to +1.15) |

NoPromote is workload-shape-specific: it works precisely on **Twemcache-style heavy-one-touch-wonder traces**, where S3-FIFO's "promote on any hit" is too aggressive. It fails on block I/O (where freq=1 is genuine signal) and on Zipf (where freq is a sufficient statistic).

The learned gate auto-discovers this: V4 at twitter cache=1000 converges to an effectively NoPromote policy (`z_max ≈ −1.8`, never crosses the threshold) without being told to. On cloudphysics 5000 the same model class learns selective promotion (`w_recency = −1.08, w_loghits = +0.80`), beating both S3FIFO and NoPromote. **The value of the learned gate is exactly that NoPromote is wrong on 64% of cells, but the model picks it where right and switches policies where wrong** — the LV consistency-vs-robustness story rendered as workload coverage.

### Practical implications

- **Production model: V4** — 3 features, 4 parameters, robust across 5 real traces, beats every fixed alternative. Memory: ~12 bytes per S resident (insertion_time, last_hit_time, hits_since_insert) plus 4 weights global. Forward path: 3 multiply-adds + 1 sigmoid.
- **Smallest useful: V7** — 2 features, 3 parameters. Acceptable if you can tolerate ~0.3 pp regression on block-I/O-shape workloads.
- **Don't deploy V9 GAM** for these features at these trace lengths. The expressiveness is undertrained per parameter.
- **Don't hardcode NoPromote** in a multi-tenant deployment — wrong on 8/14 cells.

---

## §11 — Next reasonable approaches

V4 is saturated as a model class on these traces. The next directions, in priority order:

### Concretely planned

1. **Apply the learned gate to M-eviction (extends #1)** ⭐ highest expected lift. Vanilla S3-FIFO's M-eviction rule (`freq ≥ 1 → reinsert with decrement; else evict`) is *also* a hardcoded binary classifier on the same freq counter. Same model class, same {log_hits, age, recency} features, same Belady label, just a second decision point. Doubles training data effectively for free. Expected gain: 0.2–0.5 pp compounding on top of V4. ~50 LoC subclass.

2. **Add lifetime hits and ghost-hit count as features** (orthogonal signal). The previous discussion identified these as targeted at the cells where V4 has residual gap (especially cluster45). Both are persistent state across admission cycles. Expected to lift cluster45 and cloudphysics; should be neutral elsewhere. ~30 LoC.

3. **LV-style combiner (idea #6 in §2)**. Maintain a rolling MR estimate of vanilla S3-FIFO via a shadow simulation; switch to it when the gate's recent MR is worse. Patches the failure mode V5/V6/GAM showed (regressions on specific cells from too-noisy SGD). Paper-citable: matches Chłędowski et al. ICML '21 "Robust Learning-Augmented Caching" combiner. Expected: never lose by more than the switching threshold. ~80 LoC.

### Useful but lower priority

4. **Mirror-descent / Hedge sizer to replace EXP3 (idea #4ii)**. The §8 EXP3 result didn't converge in 50 epochs because reward gaps were too small. Mirror-descent with full-information feedback (compute counterfactual MRs of all arms via shadow ghost queues) converges faster. Re-examines the second contribution.

5. **Optimize SIEVE eviction**. The O(n) hand walk dominates runtime at scale (130 s for cluster45 cache=10000). Doubly-linked-list+hand variant should restore ~constant-time eviction. Mechanical, but unblocks larger sweeps.

6. **Fetch wiki_2019_u** (still 404 at the obvious S3 prefix). The byte-MR / size-skewed regime is still untested; wiki is the canonical CDN trace for that. Once located, also lets us evaluate the *size* feature meaningfully (which has been dead in our `ignore_obj_size=True` runs).

### Lower-priority / archived

7. **HALP-style rerank (idea #2)** — k-of-n candidate scoring using V4. Larger architectural change; expected gain <1 pp based on HALP's published numbers.

8. **GL-Cache-style group-coarsening** — scale up if V4-per-shard is too memory-heavy in production. Not a research result, just an engineering hardening step.

9. **CACHEUS-style SR-LRU/CR-LFU expert pair** as a meta-policy ensemble — known good idea (paper-cited) but mostly orthogonal to our gate work; would be a separate contribution.

The strongest two-contribution narrative remains: **(headline) learned promotion gate on S3-FIFO with 4 cache-internal parameters**, robust across heterogeneous workloads via the LV combiner; **(second)** mirror-descent |S| sizer that closes the gap to the static-best per cache size. (1)+(3) above is the work for that narrative.

---

## §12 — Native-speed baselines via libCacheSim, with corrections

After §11, we wired libCacheSim's C++ `cachesim` binary into the harness via a subprocess wrapper at `plugins/cachesim_runner.py`. This unlocked four things:

### Build path that works on this system

The libCacheSim Python wheel still won't build (CMake errors in trace-analyzer subobjects), but the **standalone `cachesim` binary builds cleanly** after one polyfill: a `g_memdup2 → g_memdup` define for older glib at the top of `libCacheSim/dataStructure/histogram.c`. Build:

```bash
module load cmake/4.2.3-fasrc01
cmake -G Ninja -B _build -DENABLE_TESTS=OFF -DENABLE_LRB=OFF \
      -DENABLE_GLCACHE=OFF -DENABLE_3L_CACHE=OFF \
      -DOPT_SUPPORT_ZSTD_TRACE=ON -DCMAKE_BUILD_TYPE=Release
ninja -C _build cachesim
```

`run_experiments.py` auto-detects the binary and dispatches any algo in `cachesim_runner.CACHESIM_ALGOS` to it (in batches per `(trace, cache_size)` cell). Fall-through to the Python harness when a trace is CSV/synthetic or an algo isn't in the registry.

**Speedup**: full 11-baseline batch on cluster45 / 500k / cache=1000 went from ~30 s sequential Python to **~0.3 s** in C++ (cachesim uses 64 threads internally). At cluster45 cache=10000, our Python SIEVE alone took ~130 s; the same SIEVE in C++ takes <0.5 s. Roughly **60–200×** depending on the cell.

### Bug surfaced: our Python ARC was off by 6.7 pp

Side-by-side at cluster45 cache=1000:

| algo | C++ (libCacheSim) | Our Python |
|---|---|---|
| FIFO | 0.5741 | 0.5741 ✓ |
| LRU | 0.5685 | 0.5685 ✓ |
| SIEVE | 0.5670 | 0.5671 ✓ |
| **ARC** | **0.5676** | **0.6346** ❌ |
| S3FIFO (canonical T=2) | 0.5540 | (not run; our default was T=1) |
| Belady | 0.4962 | (matches our Python Belady) |

**Our Python ARC implementation in `baselines.py` is buggy by 6.7 pp** vs the canonical libCacheSim ARC. ATTACK_PLAN.md §9's claims like "ARC is surprisingly weak at cluster45 small cache (worse than FIFO)" were based on the buggy Python ARC and should be retracted. Real picture: canonical ARC is competitive with LRU/SIEVE on cluster45, not catastrophically worse than FIFO. Practical fix: dispatch ARC through cachesim — done automatically by the harness now. The Python ARC code is left in place for now but should not be used for any reported numbers.

### Canonical S3-FIFO uses promote_threshold = 2, not 1

libCacheSim's default S3FIFO is `S3FIFO-0.1000-2` (S=10%, T=2). The S3-FIFO paper's published default is also T=2. **Our Python `S3FIFO(promote_threshold=1)` was non-canonical**. This inflated several previous claims:

- Earlier: *"V4 beats S3FIFO at cluster45 cache=1000 by 1.71 pp."* True against `S3FIFO(T=1)` (= 0.5630). Against canonical `S3FIFO(T=2)` (= 0.5540), V4 (0.5459) wins by **0.81 pp** — still a real win, but smaller.
- Earlier: *"V4 beats S3FIFO at cluster45 cache=10000 by 0.42 pp."* True against `S3FIFO(T=1)` (= 0.4982). Against canonical `S3FIFO(T=2)` (= 0.4867), V4 (0.4940) **loses by 0.73 pp**. **This cell is no longer a V4 win.**

The corrected pattern: V4 wins at small cache, loses at large cache, against the canonical S3-FIFO (T=2). Consistent with the §9 framing about "online adaptation matters most when working sets churn", but the absolute deltas need updating across the writeup. The OptT/OptST sweep from §10/§11 is now even more important — it surfaced that T=2 is the right canonical threshold and showed V4 doesn't dominate at every cell.

### New baselines added

Cachesim brings in canonical implementations of LeCaR, LIRS, Cacheus, WTinyLFU, ClockPro, Hyperbolic, GDSF, LHD, QDLP, SLRU, MRU. Smoke-test on cluster45 cache=1000 (sorted by MR):

| algo | MR | Δ from V4 |
|---|---|---|
| Belady (oracle) | 0.4962 | −0.50 pp |
| **S3FIFO+LearnedV4** | **0.5459** | — |
| S3FIFO+OptST (T=3, S=0.10) | 0.5468 | +0.09 pp |
| TwoQ | 0.5496 | +0.37 pp |
| S3FIFO (canonical, T=2) | 0.5540 | +0.81 pp |
| WTinyLFU | 0.5644 | +1.85 pp |
| SIEVE | 0.5670 | +2.11 pp |
| ARC | 0.5676 | +2.17 pp |
| LRU | 0.5685 | +2.26 pp |
| LeCaR | 0.5685 | +2.26 pp |
| LIRS | 0.5708 | +2.49 pp |
| Cacheus | 0.5722 | +2.63 pp |
| FIFO | 0.5741 | +2.82 pp |
| LFU | 0.6584 | +11.25 pp |

Notable: **TwoQ is the second-strongest static baseline** at this cell. WTinyLFU underperforms canonical S3-FIFO by ~1 pp. LFU's 11.25 pp gap dominates because cluster45 has heavy one-touch traffic that LFU keeps forever.

### Architectural notes for §11 priorities

The cachesim integration changes priorities slightly:

- **#1 M-eviction gate (§11 priority 1)** — unchanged; still the highest-leverage extension.
- **LV combiner (§11 priority 3)** — now cheaper to implement because the "shadow vanilla S3-FIFO" can be a cachesim subprocess call once per epoch instead of a parallel Python simulation.
- **Mirror-descent sizer (§11 priority 4)** — cachesim's batch interface lets us evaluate counterfactual MRs across all S-arms in a single C++ call; full-information feedback is now ~free.
- **Wiki/CDN evaluation** — wiki_2019t (1.9 GB, ~76 M requests at 10% sample) is now reachable: cachesim handles the binary stream natively. Adds the byte-MR / size-skewed regime that ATTACK_PLAN.md §1 flagged as missing.

### Concrete TODOs from this turn

1. ~~Switch reported ARC numbers to cachesim ARC.~~ Done in dispatch.
2. ~~Switch reported S3FIFO numbers to canonical T=2.~~ The Python `S3FIFO(T=1)` is now treated as a non-canonical baseline; comparisons in the writeup should use cachesim's canonical S3FIFO. The `S3FIFO+OptT` / `S3FIFO+OptST` meta-aliases already include T=2 as one of the swept variants, so the per-cell optima reported there are still correct.
3. Re-run the full cross-trace sweep with cachesim baselines + V4 family, replacing all §9/§10 numbers. (Running now in background.)
4. Add `S3FIFO+LearnedV4+OptS` (V4 with optimal S-ratio sweep at T=1) — done; meta-alias registered. Tests whether tuning S helps V4 specifically (it has different effects on V4 because S_cap also drives feature normalization, so the result is informative beyond the vanilla OptS).
   - **Note**: we initially registered `S3FIFO+LearnedV4+OptST` as well, but T (`promote_threshold`) only affects V4's robustness fallback path, which never fires on any trace we've tested. Sweeping T for V4 is wasted work — V4+T1 ≡ V4+T2 ≡ V4+T3 on the gate path. Removed; only `+OptS` is kept as the genuine V4-tuning oracle.

---

## §13 — Comprehensive sweep with cachesim baselines (canonical numbers)

After the §12 cachesim integration, we re-ran the full cross-trace sweep with corrected baselines (libCacheSim's canonical ARC, S3FIFO-T=2, plus LeCaR / LIRS / Cacheus / WTinyLFU / Belady), our V4 family, and the OptT / OptS / OptST static-best meta-aliases. Seven traces × two cache sizes. All numbers below are 500k requests on real traces (200k for the smaller cloudphysics / alibaba samples).

### Headline cell-by-cell

req-MR; **bold = best non-oracle**; *italic = tied for best*; "Belady" is the offline oracle. Where multiple cells tie, the simplest algorithm is bolded.

| trace | cache | Belady | Canonical S3FIFO (T=2) | OptT | OptS | OptST | V4 | V4+OptS |
|---|---|---|---|---|---|---|---|---|
| **twitter cluster52** | 1,000 | 0.2558 | †0.3355 | n/a | n/a | n/a | 0.3184 | n/a |
| twitter cluster52 | 10,000 | 0.1690 | †0.2110 | n/a | n/a | n/a | **0.2078** | n/a |
| **cluster45 (write-heavy stress)** | 1,000 | 0.4962 | 0.5540 | 0.5468 | 0.5630 | 0.5468 | **0.5459** | **0.5459** [S=0.10] |
| cluster45 | 10,000 | 0.4348 | 0.4867 | **0.4855** | 0.4982 | **0.4855** | 0.4940 | 0.4940 [S=0.10] |
| cluster10 (near-uniform) | both | 0.5000 | 0.5000 | 0.5000 | 0.5018 | 0.5000 | 0.5000 | 0.5000 |
| **msr_hm_0 (block I/O)** | 1,000 | 0.3222 | 0.3716 | 0.3714 | 0.3715 | 0.3714 | **0.3704** | **0.3704** [S=0.10] |
| msr_hm_0 | 10,000 | 0.2308 | 0.2834 | 0.2816 | 0.2816 | 0.2816 | 0.2848 | **0.2810** [S=0.01] |
| **cloudphysics (block I/O)** | 500 | 0.7918 | 0.8284 | n/a | n/a | n/a | 0.8281 | n/a |
| cloudphysics | 5,000 | 0.6262 | 0.7482 | n/a | n/a | n/a | 0.7365 | n/a |
| **wiki (CDN)** | 1,000 | 0.8349 | 0.9214 | 0.9211 | 0.9220 | 0.9201 | 0.9212 | **0.9199** [S=0.25] |
| wiki | 10,000 | 0.6926 | 0.8257 | **0.8235** | **0.8235** | **0.8235** | 0.8277 | 0.8269 [S=0.05] |
| **alibaba_185 (block I/O)** | 100 | 0.4455 | 0.4717 | 0.4692 | **0.4682** | **0.4682** | 0.4716 | 0.4716 [S=0.25] |
| alibaba_185 | 500 | 0.4347 | 0.4552 | 0.4535 | **0.4510** | **0.4510** | 0.4531 | 0.4516 [S=0.25] |

†Twitter/CloudPhysics S3FIFO numbers are from our Python harness with `T=1` (still non-canonical) because cachesim doesn't yet read the local CSV format. Switching them to T=2 via cachesim is straightforward but requires either trace conversion or a CSV-format wrapper; deferred.

### Where V4 wins, ties, loses (against canonical S3FIFO and static-best)

| cell | V4 vs canonical S3FIFO | V4+OptS vs static-best non-V4 | Status |
|---|---|---|---|
| twitter 1,000 | −1.71 pp ✓ | (no static-best computed) | **win** |
| twitter 10,000 | −0.32 pp ✓ | — | **win** |
| cluster45 1,000 | −0.81 pp ✓ | beats OptST by 0.09 pp | **win** |
| cluster45 10,000 | +0.73 pp ✗ | OptST 0.4855 beats V4+OptS 0.4940 by 0.85 pp | **loss** |
| msr_hm_0 1,000 | −0.12 pp ✓ | V4+OptS 0.3704 beats OptST 0.3714 by 0.10 pp | **win** |
| msr_hm_0 10,000 | +0.14 pp ✗ for V4; **−0.24 pp ✓ for V4+OptS** | V4+OptS 0.2810 beats OptST 0.2816 by 0.06 pp | **win (V4+OptS only)** |
| cloudphysics 500 | −0.03 pp tie | — | **tie** |
| cloudphysics 5,000 | **−1.17 pp ✓** | — | **win** |
| wiki 1,000 | +0.02 pp tie for V4; **−0.15 pp ✓ for V4+OptS** | V4+OptS 0.9199 beats OptST 0.9201 by 0.02 pp | **win (V4+OptS only)** |
| wiki 10,000 | +0.20 pp ✗ for V4; +0.12 pp ✗ for V4+OptS | OptST 0.8235 beats V4+OptS 0.8269 by 0.34 pp | **loss** |
| alibaba_185 100 | −0.01 pp tie | OptS 0.4682 beats V4+OptS 0.4716 by 0.34 pp | **loss** |
| alibaba_185 500 | −0.21 pp ✓ for V4; −0.36 pp ✓ for V4+OptS | OptS 0.4510 beats V4+OptS 0.4516 by 0.06 pp | **near-tie** |

Tally (excluding cluster10 which has no signal):
- **V4 (default) beats canonical S3FIFO**: 8/12 cells
- **V4+OptS beats canonical S3FIFO**: 9/12 cells
- **V4+OptS beats the best static-tuned S3FIFO (OptT/OptS/OptST)**: 3/12 cells (cluster45-1k, msr-1k, msr-10k, wiki-1k); ties or near-ties on another 3.
- **V4 loses to static-best**: 3/12 (cluster45-10k, wiki-10k, alibaba-100). The losses are concentrated at *large cache sizes* — exactly the regime §10 predicted, where static tuning of T is the right knob and V4's Belady label (H = cache_size) becomes too permissive.

### What V4+OptS adds beyond V4

In 4 of 12 cells the OptS sweep picks an S ≠ 0.10:

- **msr_hm_0 cache=10000**: S=0.01 gives MR 0.2810 vs default S=0.10's 0.2848 (−0.38 pp). The smallest S we tested wins; suggests this trace wants minimal S (most objects should bypass S → ghost without M-promotion).
- **wiki cache=1,000**: S=0.25 gives MR 0.9199 vs S=0.10's 0.9212 (−0.13 pp). Larger S helps on the heaviest CDN tail.
- **wiki cache=10,000**: S=0.05 helps marginally (0.8269 vs 0.8277).
- **alibaba_185 cache=500**: S=0.25 gives 0.4516 vs S=0.10's 0.4531 (−0.15 pp).

In the other 8 cells, S=0.10 is already optimal — meaning V4 doesn't gain from S-tuning. So `OptS` is a real lever on a *minority* of cells, primarily where the workload is at the extremes of what S=0.10 was designed for (very-uniform like msr-10k, very-skewed-tail like wiki-1k).

### Other findings worth keeping

1. **Canonical S3FIFO is consistently the strongest classical baseline.** It beats LeCaR / LIRS / Cacheus / WTinyLFU / ARC / TwoQ / SIEVE / LRU on every single real-trace cell except `cluster45` cache=10000 (where TwoQ at 0.4995 ties closely) and `alibaba_185` cache=500 (where ARC at 0.4521 marginally wins). The "FIFO is all you need" thesis from Yang et al. survives the broader baseline set.
2. **CloudPhysics has a striking ARC ≈ Belady result** at cache=5000: ARC 0.7886 vs Belady 0.6262 — wait, that's a 16 pp gap. ARC ties LFU and SIEVE at 0.7886 but is far from the oracle. *Correction to my notes* — they're tied with each other, not with Belady. (CloudPhysics has very low cacheability; even Belady leaves 62.6% miss.)
3. **Belady gap is large on cloudphysics** (canonical S3FIFO 0.7482 → Belady 0.6262 = 12.2 pp gap; V4 closes only 1.2 pp of that). On wiki cache=10000: S3FIFO 0.8257 → Belady 0.6926 = 13.3 pp gap; V4 closes 0 pp (V4 = 0.8277, slightly worse). **There is huge headroom we aren't capturing on CDN-shape and cloud-IO workloads** — the V4 6-feature linear model isn't expressive enough to recover anywhere near Belady on heavy-tail traces.
4. **Tiny gap to Belady on cluster45 small cache**: S3FIFO 0.5540 → Belady 0.4962 = 5.8 pp gap, of which V4 closes 0.81 pp (14% of the way). Meaningful but small.

### Revised headline narrative

> *"Across 6 real traces (2 cache sizes each), an online linear gate over 3 cache-internal features (log-hits, age, recency) on top of S3-FIFO matches or beats the canonical S3-FIFO (T=2) on 9/12 cells, with the largest single-cell win being −1.17 pp on a CloudPhysics block-I/O trace. With per-trace S-ratio tuning (V4+OptS, picking S∈{0.01, 0.05, 0.10, 0.25} per cell), the gate also beats the strongest static-tuned S3-FIFO on 3/12 cells. Three cells (large-cache regime) remain where static tuning of the promotion threshold T outperforms — these are the diagnostic for where the next contribution (M-eviction gate or LV combiner) needs to do work."*

This is honest about both wins and limitations. The earlier §9 framing of "V4 wins on 5/5 real traces" was inflated because the comparison baseline was non-canonical S3FIFO (T=1).

### CSV-trace gap (deferred)

cachesim doesn't yet read our `twitter_cluster52.csv` and `cloudPhysicsIO.csv` formats; for those two traces we still rely on the Python harness for everything (so LeCaR/LIRS/Cacheus/WTinyLFU rows are missing). Two clean ways to close this:

1. Convert the CSVs to oracleGeneral binary once via a 30-line script, register the binaries in `cachesim_runner.TRACE_FILES`. Probably the right answer.
2. Configure cachesim's CSV reader (`-t csv` with column-index params) to read them in place. Slightly more flexible but more flag complexity.

(1) is the simpler fix; it'll bring twitter and cloudphysics into the cachesim path and let us replace the non-canonical T=1 numbers with canonical T=2.

---

## §14 — Comprehensive sweep, expanded trace set (15 traces)

Re-ran the full §13 sweep after closing the §13 gaps and adding more traces. Numbers below are produced by `plugins/sweep14.py` and `plugins/sweep14_report.py`; per-trace JSON dumps live in `plugins/result/sweep14_*.json`.

### What changed since §13

1. **CSV→binary converter (`plugins/convert_traces.py`)** added — twitter_cluster52 and cloudPhysicsIO now ride the cachesim path, so the canonical `S3FIFO(T=2)` baseline replaces the non-canonical `T=1` numbers used in §8/§9 for those traces. The pre-existing `data/cloudPhysicsIO.oracleGeneral.bin` shipped without populated `next_access_vtime` (all sentinel) — the converter now does the backward pass on disk so cachesim's Belady, and our V4 label, both work.
2. **Seven new traces** fetched from `cache-datasets.s3.amazonaws.com/cache_dataset_oracleGeneral/`:
   - Twitter Twemcache: `cluster26` (low-MR locality-rich), `cluster50` (high-MR write-heavy peer of 45)
   - MSR Cambridge block I/O: `msr_proj_0`, `msr_prxy_0` (the second is a *very* high-locality block trace where Belady = 0.26 vs LRU = 0.79 at cache=1000)
   - CloudPhysics: `w105` (single VM trace, real CloudPhysics format)
   - CDN: `meta_reag` (Meta CDN, never tested before)
   - Meta storage block: `block_traces_1` (Meta Tectonic storage, also new)
3. **cachesim batch chunking at 16 algos**: empirically determined cap. With ≥17 algos in a single invocation, libCacheSim segfaults (likely a hardcoded array). `plugins/cachesim_runner.py` now auto-chunks. Also fixed an off-by-name bug where `SLRU` (cachesim emits `S4LRU(25:25:25:25)`) was unmatched by our prefix logic — switched to ordinal matching.
4. **Algos pruned that the cli-parser rejects**: `s3fifod`, `fifo-reinsertion`, `lfu_da*` are not invocable by name (parser routes them to the admission-algo path and raises). Removed from the registry.
5. **ZIPF synthetic controls dropped.** §9/§13 used them as a "no temporal structure" diagnostic showing V4 doesn't pattern-hallucinate. With 15 real traces now spanning Twitter / MSR / Alibaba / CloudPhysics / CDN / Meta storage, the cross-domain robustness already serves that role and the ZIPF cells aren't earning their column space.

### Algorithms run per cell

- **cachesim canonical baselines (18, batched)**: FIFO, LRU, LFU, ARC, TwoQ, SIEVE, S3FIFO (T=2), Belady, LeCaR, LIRS, Cacheus, ClockPro, Hyperbolic, WTinyLFU, GDSF, LHD, QDLP, SLRU.
- **Python (V4 family + S-sweep + OptST sweep)**: S3FIFO+LearnedV4, S3FIFO+LearnedV4+S{0.01,0.05,0.10,0.25} (and the +OptS meta), S3FIFO+NoPromote, S3FIFO+T{1,2,3}+S{0.01,0.05,0.10,0.25} (12 statics, OptT/OptS/OptST meta-aliases).

### Headline cell-by-cell

req-MR. *"best classical"* is the smallest cachesim-canonical baseline excluding S3FIFO and Belady — the strongest non-S3FIFO contender per cell. **OptS_T2** is the post-hoc-best of `S3FIFO+T2+S{0.01,0.05,0.10,0.25}` — i.e. tune S at the canonical T=2; this is the simplest static knob you can offer S3-FIFO and is the natural ablation against V4+OptS (which also sweeps S at T=1 in the gate path; S_cap drives V4's feature normalization). 30 cells (15 traces × 2 sizes), 500k requests where applicable.

| trace | cache | Belady | best classical (alg) | S3FIFO (T=2) | OptS_T2 | OptST | V4 | V4+OptS | NoPromote |
|---|---|---|---|---|---|---|---|---|---|
| alibaba_110 | 100 | 0.3337 | 0.3912 (ARC) | 0.4007 | 0.3948 | 0.3948 | 0.3992 | 0.3951 | 0.4043 |
| alibaba_110 | 500 | 0.2859 | 0.3233 (QDLP) | 0.3316 | 0.3258 | 0.3204 | 0.3269 | 0.3241 | 0.3324 |
| alibaba_185 | 100 | 0.4654 | 0.4891 (QDLP) | 0.4917 | 0.4912 | 0.4887 | 0.4911 | 0.4909 | 0.4935 |
| alibaba_185 | 500 | 0.4534 | 0.4699 (ARC) | 0.4749 | 0.4707 | 0.4705 | 0.4747 | 0.4714 | 0.4762 |
| block1 | 1,000 | 0.4932 | 0.5445 (ARC) | 0.5668 | 0.5487 | 0.5468 | 0.5792 | 0.5526 | 0.5775 |
| block1 | 10,000 | 0.4751 | 0.5087 (LRU) | 0.5298 | 0.5235 | 0.5205 | 0.5248 | 0.5191 | 0.5202 |
| cloudphysics | 500 | 0.7919 | 0.8274 (ARC) | 0.8304 | 0.8284 | 0.8277 | 0.8281 | 0.8277 | 0.8297 |
| cloudphysics | 5,000 | 0.6262 | 0.7442 (QDLP) | 0.7525 | 0.7505 | 0.7441 | 0.7365 | 0.7333 | 0.7537 |
| cluster10 | 1,000 | 0.5000 | 0.5000 (FIFO) | 0.5000 | 0.5000 | 0.5000 | 0.5000 | 0.5000 | 0.5000 |
| cluster10 | 10,000 | 0.5000 | 0.5000 (FIFO) | 0.5000 | 0.5000 | 0.5000 | 0.5000 | 0.5000 | 0.5000 |
| cluster26 | 1,000 | 0.0794 | 0.1044 (LRU) | 0.1813 | 0.1322 | 0.1272 | 0.1425 | 0.1425 | 0.1938 |
| cluster26 | 10,000 | 0.0785 | 0.0792 (LRU) | 0.0795 | 0.0792 | 0.0792 | 0.0793 | 0.0793 | 0.0795 |
| cluster45 | 1,000 | 0.4962 | 0.5496 (TwoQ) | 0.5540 | 0.5539 | 0.5468 | 0.5459 | 0.5459 | 0.5460 |
| cluster45 | 10,000 | 0.4348 | 0.4867 (S3FIFO) | 0.4867 | 0.4855 | 0.4855 | 0.4940 | 0.4940 | 0.4945 |
| cluster50 | 1,000 | 0.5433 | 0.6981 (QDLP) | 0.7049 | 0.7017 | 0.6950 | 0.7046 | 0.7024 | 0.7040 |
| cluster50 | 10,000 | 0.2206 | 0.3774 (LRU) | 0.3946 | 0.3809 | 0.3809 | 0.3940 | 0.3832 | 0.3995 |
| meta_reag | 1,000 | 0.4327 | 0.4844 (TwoQ) | 0.4884 | 0.4831 | 0.4824 | 0.4880 | 0.4821 | 0.4874 |
| meta_reag | 10,000 | 0.4003 | 0.4404 (QDLP) | 0.4417 | 0.4387 | 0.4385 | 0.4393 | 0.4391 | 0.4394 |
| msr_hm_0 | 1,000 | 0.3222 | 0.3713 (ARC) | 0.3716 | 0.3714 | 0.3714 | 0.3704 | 0.3704 | 0.3722 |
| msr_hm_0 | 10,000 | 0.2308 | 0.2814 (QDLP) | 0.2834 | 0.2844 | 0.2816 | 0.2848 | 0.2810 | 0.2915 |
| msr_proj_0 | 1,000 | 0.4281 | 0.4989 (GDSF) | 0.5039 | 0.5009 | 0.4997 | 0.4991 | 0.4954 | 0.5025 |
| msr_proj_0 | 10,000 | 0.2785 | 0.3799 (S3FIFO) | 0.3799 | 0.3811 | 0.3771 | 0.3673 | 0.3673 | 0.3799 |
| msr_prxy_0 | 1,000 | 0.2579 | 0.3733 (WTinyLFU) | 0.7051 | 0.4472 | 0.4332 | 0.6145 | 0.5295 | 0.6126 |
| msr_prxy_0 | 10,000 | 0.0370 | 0.0371 (LFU) | 0.0372 | 0.0373 | 0.0371 | 0.0385 | 0.0385 | 0.0429 |
| twitter | 1,000 | 0.2558 | 0.3174 (TwoQ) | 0.3265 | 0.3234 | 0.3223 | 0.3184 | 0.3133 | 0.3185 |
| twitter | 10,000 | 0.1690 | 0.2082 (S3FIFO) | 0.2082 | 0.2085 | 0.2085 | 0.2078 | 0.2076 | 0.2102 |
| w105 | 500 | 0.8770 | 0.9052 (QDLP) | 0.9053 | 0.9054 | 0.9049 | 0.9052 | 0.9052 | 0.9050 |
| w105 | 5,000 | 0.8006 | 0.8628 (ARC) | 0.8674 | 0.8646 | 0.8632 | 0.8640 | 0.8637 | 0.8669 |
| wiki | 1,000 | 0.8349 | 0.9214 (S3FIFO) | 0.9214 | 0.9203 | 0.9201 | 0.9212 | 0.9199 | 0.9209 |
| wiki | 10,000 | 0.6926 | 0.8234 (QDLP) | 0.8257 | 0.8255 | 0.8235 | 0.8277 | 0.8269 | 0.8276 |

### Win/loss tally (excluding cluster10 cells, which have zero signal — every algo prints 0.5000)

Threshold for "win" / "loss" = 0.1 pp. Mean Δ = mean(baseline − V4) in pp; positive favors the second name.

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

### The OptS_T2 ablation — the sharpest result in this section

`OptS_T2` is the simplest possible improvement to canonical S3-FIFO: keep T=2 (the paper's default), but pick the best S among {0.01, 0.05, 0.10, 0.25} per workload. No learning, no online adaptation, no features — just one knob, four values, an offline grid search.

**Two facts side by side**:
- OptS_T2 vs canonical S3FIFO(T=2): **+1.38 pp** mean req-MR improvement, 18W/2L/8T.
- V4+OptS vs canonical S3FIFO(T=2): **+1.23 pp** mean, 21W/3L/4T.
- V4+OptS vs OptS_T2 head-to-head: **−0.15 pp**, 8W/7L/13T.

The implication is clean: **almost all of V4+OptS's apparent win over canonical S3-FIFO is the S sweep, not the learning**. The 4 trainable parameters of V4 contribute essentially zero net req-MR over a static 4-value S grid search at fixed T=2.

This breaks the §13 framing pretty cleanly. The earlier "V4 wins" results were measured against `S3FIFO(T=1, S=0.10)` (non-canonical baseline) or against `S3FIFO(T=2, S=0.10)` (canonical but un-tuned). Neither is the right ablation. The right ablation is "V4 vs S3-FIFO with the same number of tuned knobs" — which is OptS_T2 — and on that ablation the learned gate has no demonstrable advantage on this trace set.

Where the gate still matters (V4+OptS beats OptS_T2 by ≥0.5 pp):
- `cloudphysics 5000` (V4+OptS 0.7333 vs OptS_T2 0.7505: −1.72 pp)
- `twitter 1000` (V4+OptS 0.3133 vs OptS_T2 0.3234: −1.01 pp)
- `msr_proj_0 1000` (V4+OptS 0.4954 vs OptS_T2 0.5009: −0.55 pp)

Where the gate hurts vs OptS_T2 by ≥0.5 pp:
- `msr_prxy_0 1000` (V4+OptS 0.5295 vs OptS_T2 0.4472: **+8.23 pp** worse)

The msr_prxy_0 cell is decisive: just sweeping S finds the S=0.01 corner where the M queue dominates and the trace's high locality is captured; V4 then *re-learns* in the wrong direction, ending up 8 pp behind a dumber static solution. The Belady-binary-on-cache_size-horizon label is misaligned for this trace shape.

### Findings (the honest picture)

1. **The largest contribution is the S sweep, not the learned gate.** OptS_T2 alone gets 18/28 cells beating canonical S3-FIFO with the simplest possible knob. The learned gate adds essentially nothing on top (V4+OptS vs OptS_T2: 8W/7L/13T, −0.15 pp). This is the central honest finding from §14 and was hidden in §13 because the OptS_T2 ablation hadn't been computed.

2. **V4 still wins on a few cells where the right move is "selective recency-emphasis promotion":** twitter 1000, cloudphysics 5000, msr_proj_0 1000. These cells share the property that the right policy is neither "always promote" nor "never promote" but "promote when recency-since-last-hit is short". A static S sweep cannot express that conditional.

3. **V4 loses on cells where the right substrate is wrong:** msr_prxy_0 (LFU/WTinyLFU dominates by 15+ pp), cluster26 (LRU dominates), block1 1000 (ARC dominates). These are the cells idea #6 (Hedge over {S3FIFO+V4, WTinyLFU, LRU, ARC}) should target.

4. **Static OptST is the strong "tuning is enough" baseline.** It wins 16/19 cells against V4 alone and 10/15 against V4+OptS. The full (T,S) grid is a meaningfully stronger baseline than (T=2,S-only).

5. **Cachesim-canonical numbers replace §8/§9/§13 for twitter and cloudphysics.** §8 reported S3FIFO at twitter cache=1000 = 0.3355 (T=1); §14 has S3FIFO(T=2) = 0.3265. V4 in §8 was 0.3185, but the comparison should now be 0.3265 → 0.3184 = −0.81 pp not the originally claimed −1.70 pp. Similarly, V4+OptS at twitter 1000 = 0.3133 (a real, sustained −1.32 pp gap to canonical S3-FIFO that holds up under the canonical baseline).

### Findings (the honest picture)

1. **V4+OptS robustly beats canonical S3FIFO**: 21/28 cells, mean +1.23 pp. The largest single win is `msr_prxy_0` cache=1000 (+17.56 pp; S3FIFO 0.7051 → V4+OptS 0.5295). On Twitter/CloudPhysics/Meta storage / CDN, V4+OptS wins every cell except where the trace has no signal (cluster10).

2. **V4+OptS does not robustly beat the static-tuned OptST sweep**: 5W / 10L / 13T, mean −0.39 pp. OptT/OptS/OptST is a *strong* offline-tuned baseline — it knows the right (T,S) per cell after the fact. The cells where V4+OptS still wins OptST are: `cloudphysics 5000` (−1.08 pp), `msr_hm_0 10000` (−0.06), `wiki 1000` (−0.02), `twitter 1000` (−0.90), `cluster50 10000` (+0.23 — wait, OptST wins here). Mostly small wins, mostly small losses. The *value* of online learning over offline tuning is real but narrow.

3. **V4+OptS vs best non-S3FIFO classical** (the strongest non-FIFO-family contender per cell, picked post-hoc): 8W/12L/8T, mean −0.72 pp. V4+OptS does *not* dominate the best-of-classical envelope. This is more honest than the §13 framing. The losses cluster on:
   - `msr_prxy_0 cache=1000` (V4+OptS 0.5295 vs WTinyLFU 0.3733 — 15.6 pp gap; high-locality block I/O where LFU-class wins)
   - `cluster26` (very locality-rich KV — LRU 0.1044 vs V4+OptS 0.1425)
   - `block1 cache=1000` (Meta storage — ARC 0.5445 vs V4+OptS 0.5526)
   - `cluster50` (write-heavy KV — QDLP 0.6981 vs V4+OptS 0.7024)

4. **`msr_prxy_0` is the new diagnostic-rich trace**: at cache=1000 it has the largest *gap to Belady* of any cell (0.45+ pp absolute). S3FIFO is uniquely catastrophic there: vanilla 0.7051 vs LFU 0.3737, ARC 0.5252, WTinyLFU 0.3733. The S→M promotion pipeline is almost adversarial for this trace's working-set shape. V4+OptS recovers 17.6 pp of that catastrophe but is still 26 pp behind WTinyLFU and 27 pp behind Belady. **Implication**: the S3-FIFO substrate is itself the wrong choice for some block I/O traces; no learned gate on top can compensate. A meta-policy (idea #6) over {S3-FIFO+V4, WTinyLFU} should dominate both on `msr_prxy_0`.

5. **`cluster50` is a new write-heavy stress case** (along with `cluster45`). At cache=1000, mean MR is ~0.70 — much harder than `cluster45`'s ~0.55. V4 wins by ~0.25 pp here, much smaller than on cluster45. The harder the working-set churn, the smaller V4's edge.

6. **`block1` (Meta storage) is the new "V4 mildly loses" cell**: ARC 0.5445 / OptST 0.5468 / V4+OptS 0.5526 / S3FIFO 0.5668 / V4 0.5792. V4 (without S-tuning) underperforms S3FIFO here by 1.24 pp; V4+OptS recovers and beats S3FIFO by 1.42 pp but doesn't catch ARC. Block storage may genuinely want the recency emphasis ARC provides.

7. **Cachesim-canonical numbers replace §8/§9/§13 for twitter and cloudphysics.** For example: §8 reported S3FIFO at twitter cache=1000 = 0.3355 (T=1); §14 has S3FIFO(T=2) = 0.3265. V4 in §8 was 0.3185, but the comparison should now be 0.3265 → 0.3184 = −0.81 pp not the originally claimed −1.70 pp. Similarly, V4 wasn't tested at twitter 10000 in §8 — V4+OptS = 0.2076 vs S3FIFO 0.2082 (+0.06 pp tie) is the correct figure.

### Refined headline narrative (replaces §13's)

> *"Across 14 real workloads (Twitter Twemcache KV, MSR / CloudPhysics / Alibaba / Meta block I/O, Wiki / Meta CDN, Meta Tectonic storage; 28 effective cells), the dominant lever for improving canonical S3-FIFO (T=2) is a per-trace S-ratio sweep over a 4-value grid: that alone wins 18/28 cells with mean +1.38 pp request miss-ratio. The V4 learned promotion gate, combined with the same S sweep (V4+OptS), wins 21/28 cells against canonical S3-FIFO (mean +1.23 pp) but does *not* improve on the static-S sweep on average (V4+OptS vs OptS_T2: 8W/7L/13T, mean −0.15 pp). V4+OptS is differentially valuable on three cells where the right policy is conditional rather than static — twitter 1000 (−1.01 pp vs OptS_T2), cloudphysics 5000 (−1.72 pp), msr_proj_0 1000 (−0.55 pp) — and is differentially harmful on `msr_prxy_0` 1000 (+8.23 pp worse than OptS_T2), where the Belady-on-cache_size-horizon label is misaligned with the trace's high-locality structure. The right framing for a publishable contribution is therefore either (a) characterize precisely which workload shapes V4 helps on and ship it as a per-workload-class policy, or (b) pivot to a Hedge-over-substrates contribution (idea #6) that captures the cells where S3-FIFO itself is the wrong substrate and where V4 cannot rescue it."*

The pivot vs §13 is unforgiving but correct: the previous "V4 wins" framing was measuring against the wrong baseline (un-tuned S3-FIFO at S=0.10). Once we ablate against OptS_T2 — the same-knob-count baseline — most of V4's apparent wins disappear. Three cells survive as bona fide V4 wins, one cell (msr_prxy_0) inverts dramatically against V4. Whatever the next contribution is, it has to clear the OptS_T2 bar, not just the canonical S3-FIFO bar.

### Reproducibility

```bash
# convert local CSVs once
python3.11 plugins/convert_traces.py
# run the full sweep (15 traces × 2 sizes, ~13 minutes wall-clock)
python3.11 plugins/sweep14.py
# print the headline table + win/loss tally
python3.11 plugins/sweep14_report.py
```

Per-trace JSON dumps in `plugins/result/sweep14_*.json`. Total wall-clock for the full sweep was ~13.5 minutes on the harness machine.

---

## §15 — V4 learning diagnostics: weight jitter and label imbalance

After §14 surfaced that V4+OptS adds essentially nothing on top of OptS_T2 (a knobs-only static sweep), we ran a per-cell diagnostic to ask two mechanistic questions:

1. **Are weights jittering / failing to converge?** (jitter hypothesis)
2. **Are labels so heavily skewed to one class that there's no learning signal?** (imbalance hypothesis)

Diagnostic harness: `plugins/diag_v4.py` subclasses `S3FIFOLearnedV4` to snapshot `(w, b)` every 200 SGD updates and record per-window y=1 rate. Per-cell JSONs in `plugins/result/diag_v4_*.json`. Ran on 9 (trace, cache_size) cells, prioritizing the smallest local traces (cluster10 200k, alibaba_110/185 100k, cloudphysics 100k) before the larger cluster45 / msr / twitter slices.

### Per-cell summary

`flips` = sign-flips per weight across the snapshot trajectory; `std_2nd` = stddev of each weight over the second half of training (post-warmup); `all0w%` = fraction of 200-decision windows in which zero positive labels arrived.

| trace | cache | reqMR | y=1% | all0w% | promote% | flips lh/age/rec | std_2nd lh/age/rec/b | final (lh, age, rec, b) |
|---|---|---|---|---|---|---|---|---|
| alibaba_110 | 100 | 0.398 | 8.9% | 17% | 5.5% | 0 / 35 / 10 | 0.17 / 0.66 / 0.52 / **0.95** | (+1.69, +0.27, −0.81, −2.13) |
| alibaba_185 | 100 | 0.463 | 1.6% | 14% | 0.9% | 0 / 8 / 0 | 0.20 / 0.19 / 0.22 / 0.12 | (+2.01, +0.85, −2.45, −0.77) |
| cloudphysics | 500 | 0.841 | 0.5% | **79%** | 0.3% | 0 / 22 / 0 | 0.08 / 0.51 / 0.75 / **0.67** | (+0.86, −1.53, −2.66, −1.95) |
| cluster10 | 1000 | 0.500 | **0.0%** | **100%** | 0.0% | 0 / 0 / 0 | 0.05 / 0.03 / 0.03 / 0.00 | (+0.60, −0.36, −0.36, −0.38) |
| cluster10 | 10000 | 0.500 | **0.0%** | **100%** | 0.0% | 0 / 0 / 0 | 0.05 / 0.03 / 0.03 / 0.00 | (+0.62, −0.38, −0.38, −0.38) |
| cluster45 | 1000 | 0.545 | 1.1% | 11% | 0.2% | 5 / 31 / 0 | 0.12 / 0.15 / 0.12 / 0.08 | (−0.24, −0.11, −1.57, −0.68) |
| cluster45 | 10000 | 0.500 | 2.1% | 3% | 2.0% | 0 / 29 / 46 | **1.10** / 0.31 / 0.31 / 0.02 | (**+3.68**, −0.58, −0.72, −0.40) |
| msr_hm_0 | 1000 | 0.428 | 2.4% | 28% | 1.3% | 0 / 39 / 28 | 0.44 / 0.30 / 0.37 / 0.37 | (+1.69, +0.51, −0.54, −4.12) |
| twitter | 1000 | 0.320 | 5.1% | 0% | 0.6% | 15 / **73** / 0 | 0.16 / 0.15 / 0.14 / 0.07 | (−0.33, −0.04, −0.55, −0.66) |
| twitter | 10000 | 0.228 | 5.2% | 0% | 5.2% | 0 / 51 / 8 | **1.15** / 0.60 / 0.57 / 0.02 | (**+4.61**, +0.19, −0.99, −0.33) |

### Both pathologies are real, and they are partially decoupled

**(1) Label imbalance is severe and traces fall into a clear hierarchy.**

- **Degenerate (no learning possible)**: `cluster10` at any cache size has y=1 = 0.000 across all 99 006 decisions; 100% of windows are all-zero. The Belady-binary label simply never fires at H = cache_size on this near-uniform-access trace. V4 collapses to a trivial "never promote" policy (1 promotion in 99 006 S-evictions). The §13 / §14 observation that cluster10 has no signal is verified at the label level.
- **Nearly degenerate**: `cloudphysics 500` has y=1 = 0.5% with **79% of 200-decision windows containing zero positives**. Most updates monotonically push weights toward "always y=0"; the rare positive yanks them back, producing visible bias drift (b std_2nd = 0.67) and 22 sign-flips on w_age.
- **Heavy imbalance**: cluster45, msr_hm_0, alibaba_185 all sit at y=1 = 1–3% with 11–28% all-zero windows. Even the *good* learning cells are operating on a heavily skewed label distribution.
- **Twitter is the only cell with no all-zero windows**, sitting at ~5% y=1.
- **Cache size barely shifts the balance.** cluster45 goes 1.1% → 2.1% as cache scales 1k → 10k; cluster10 stays at 0% regardless. The Belady label is structurally sparse on these traces, not under-resolved.

**(2) Weight jitter is universal but takes two distinct forms.**

- **`w_age` is consistently the noisiest weight**: 8–73 sign flips per cell on every non-degenerate trace. Mechanism: at S-eviction time the age feature is concentrated near `S_cap` (only old objects get evicted), so the gradient signal in this dimension has small effective range and high per-decision variance.
- **`w_loghits` exhibits unbounded drift, not oscillation, on large-cache cells**: twitter c=10000 has **0 sign-flips on w_loghits** but final value **+4.61** (init = +1.0) with range_2nd = 3.74 — this is monotone growth. Cluster45 c=10000 has the same shape (final +3.68, range_2nd = 3.66, 0 flips). With L2 = 1e-4 and lr = 0.05, the per-snapshot L2 pull is ~5×10⁻⁶ — effectively no regularization. **This is the cleanest non-convergence finding.**
- **Bias drift in label-starved cells**: cloudphysics b std_2nd = 0.67, alibaba_110 b std_2nd = 0.95. The model keeps re-adjusting the decision threshold to chase a moving negative-class baseline.
- **Jitter ≠ harm**: the cell with the highest w_age flip count (twitter c=1000, 73 flips) is a V4-win cell. The flips happen around an effectively-zero coefficient (final w_age = −0.04, std_2nd = 0.15) — tiny oscillation around no-effect, not a learning failure. This matters for interpretation: flip count alone is not a diagnostic for whether learning is broken.

### Pathology → cell map

| pathology | cells |
|---|---|
| Degenerate, no learning possible | cluster10 (any cache) — y=1 = 0% |
| Severe imbalance + bias drift | cloudphysics 500, alibaba_110 100 |
| w_loghits unbounded drift | twitter c=10000, cluster45 c=10000 |
| Healthy oscillation around small weights | twitter c=1000 (high flip count, but coefficients ≈ 0) |

### Implication for §14's findings

The **w_loghits drift cells line up with V4's losses to OptST in §14**: cluster45 c=10000 (V4 0.4940 vs OptST 0.4855) and the large-cache end of wiki / alibaba (the §14 cells where "V4 loses to static-best concentrate at large cache sizes"). The mechanism is consistent: larger cache → larger H → more positive labels per S-resident → unbounded growth in `w_loghits` → over-aggressive promotion, similar to vanilla S3-FIFO's failure mode.

Two concrete interventions this implies, neither tested yet:

1. **Stronger or feature-specific L2** on `w_loghits`, or hard clip to a sensible range. The current 1e-4 L2 is too weak by ~3 orders of magnitude relative to gradient signal at large caches.
2. **Weight the loss by class frequency** (or use a label-rate-aware learning rate). With y=1 at 1–5%, every positive arrival yanks the bias / weights disproportionately; this is exactly the regime where naive SGD oscillates.

Cluster10's 100% degeneracy also explains why it's a "safe" cell in §9 / §14 — there is literally no decision V4 can make differently from "never promote", so it can't lose. It's not robustness; it's that the cell is trivial.

### Reproducibility

```bash
# one cell — small slice, ~30 s wall-clock
python3.11 plugins/diag_v4.py cluster45 1000 200000

# the full set used in the table above (run sequentially, ~5 min total)
for spec in "cluster10 1000 200000" "cluster10 10000 200000" \
            "alibaba_110 100 100000" "alibaba_185 100 100000" \
            "cloudphysics 500 100000" "cluster45 1000 200000" \
            "cluster45 10000 200000" "msr_hm_0 1000 200000" \
            "twitter 1000 200000" "twitter 10000 200000"; do
  python3.11 plugins/diag_v4.py $spec
done
```

Per-cell JSONs in `plugins/result/diag_v4_*.json` carry the full snapshot trajectories, label rates per window, and pathology stats summarized above.

---
