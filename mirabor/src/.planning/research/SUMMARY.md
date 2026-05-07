# Research Synthesis — Milestone 2 Extensions

**Project:** civicache (CS 2640 Final Project, Spring 2026)
**Date:** 2026-04-16
**Overall confidence:** HIGH on architecture + API contracts (Context7-verified); HIGH on W-TinyLFU structural design (Caffeine wiki-verified); MEDIUM on exact W-TinyLFU parameter constants; MEDIUM on published-paper figures (web search was unavailable during features pass).

---

## Executive Summary

The research converges on an opinionated picture. The base simulator is clean and extensible, so every new piece slots into the existing strategy-pattern shape (`CachePolicy` subclass, header-only, single-trace-per-invocation, CSV-as-contract). The only architectural bend is W-TinyLFU, which is larger than the other five policies combined and gets its own header (`include/wtinylfu.h`) with two supporting headers (`count_min_sketch.h`, `doorkeeper.h` — the last optional). Cross-workload comparison is pure Python post-processing over two independently-produced result trees (`results/congress/`, `results/court/`); the C++ driver stays single-trace.

The court-records trace source is settled: **CourtListener REST v4** at `https://www.courtlistener.com/api/rest/v4/`, not PACER. PACER is paywalled ($0.10/page, ~$500-$2,000 for a 20K trace) and gated behind SSN/EIN billing verification; CourtListener is free to authenticated users at 5,000 queries/hour with `Authorization: Token <token>`. The collector mirrors the existing Congress.gov pattern with ~0.8s base delay + 0-0.4s jitter (faster than Congress's 1.2s because the quota is 5× looser) and emits the same `timestamp,key,size` CSV schema. Three rich endpoints (`docket-entries`, `recap-documents`, `recap-query`) are gated "select users only" — avoid them; the public endpoints (`dockets`, `opinions`, `clusters`, `courts`) suffice and give the desired size-distribution contrast vs Congress bill JSON.

W-TinyLFU is the riskiest new code. The recommendation is **header-only C++17 with roll-your-own CMS + optional Doorkeeper** (no Boost/Abseil/xxhash), using Caffeine defaults: 4-bit counters, 4 rows, width = next-pow2 ≥ cache-size-in-objects, periodic halving every `10×W` accesses, 1%/99% window/main, 80%/20% protected/probation SLRU. Hill-climbing is explicitly rejected (orthogonal, adds ~200 LoC, complicates comparison). A deterministic FNV-1a hash extracted from `src/shards.cpp` into a new `include/hash_util.h` is a prerequisite; `std::hash<std::string>` is banned (libstdc++-implementation-specific, breaks reproducibility).

---

## Key Findings by Research Dimension

**Stack (HIGH confidence):** No new languages, no new Python deps, no new C++ deps. New env var `COURTLISTENER_API_KEY`. Endpoints to hit: `/dockets/{id}/`, `/opinions/{id}/`, `/clusters/{id}/`, `/courts/{court_id}/` (suggested weighting 60/25/10/5). Endpoints to avoid: `/docket-entries/`, `/recap-documents/`, `/recap-query/`, `/recap-fetch/`. Rate cadence: 0.8s base + 0-0.4s jitter → ~3,200 req/hr (~36% headroom).

**Features (MEDIUM confidence on numerics — verify before citing papers):** Table stakes = object+byte MRC, **throughput/accesses-per-second (NEW infrastructure, highest-leverage single add)**, workload characterization table (extend with CourtListener row), multiple cache sizes, 6-policy baseline, reproducibility package, "winner per regime" statement. Differentiators = cross-domain comparison table, miss-ratio-vs-throughput Pareto plot, SHARDS error bands + error-scaling-with-trace-length figure, alpha/OHW sensitivity, size-distribution vs byte-MRC divergence, **methodological ablation (W-TinyLFU with vs without Doorkeeper)**, multi-seed CIs, AI-use report as first-class deliverable. Anti-features: no production service, no multi-tenant, no real-server latency, no real CDN traces, no additional policies beyond W-TinyLFU, no multi-threading, no third trace domain.

**Architecture (HIGH confidence):** Extend, don't restructure. New includes: `hash_util.h` (extract FNV-1a from `src/shards.cpp:85-98` FIRST), `count_min_sketch.h`, `doorkeeper.h` (optional), `wtinylfu.h`. `cache.h` gets one `#include "wtinylfu.h"` at the bottom. `main.cpp::make_policy()` gets one `wtinylfu` branch. New scripts: `scripts/collect_court_trace.py` (separate file, NOT a polymorphic `--source congress|court` generalization), `scripts/compare_workloads.py`. New results layout: per-trace subdirs `results/{congress,court,shards_large,compare}/`. Simulator stays single-trace; cross-workload comparison is Python post-processing. W-TinyLFU uses nested inline structs (NOT delegating to `LRUCache` — would double-count stats). Capacity is bytes throughout. SHARDS 1M validation is by **self-convergence across sampling rates**, NOT vs exact oracle (existing 50K guard at `main.cpp:289` correctly skips exact path).

**Pitfalls (HIGH confidence on C1-C4, C5-C6; MEDIUM on writeup):** 7 critical, 6 moderate, 6 minor. Critical: C1 PACER/CourtListener confusion; C2 5k/hr throttle; C3 gated endpoints (403); C4 10-min server-side query cache; C5 CMS without aging (stale forever); C6 window/SLRU/admission edge cases (±2-5% error looking like "different workload"); C7 live demo fails on unfamiliar environment.

---

## Key Decisions (locked in)

| Decision | Choice |
|----------|--------|
| Court API | **CourtListener REST v4** — `https://www.courtlistener.com/api/rest/v4/`, token auth, free, 5k/hr |
| Auth env var | `COURTLISTENER_API_KEY` (parallel to `CONGRESS_API_KEY`) |
| Collector cadence | 0.8s base + 0-0.4s jitter (~1.1s mean) |
| Collector script | Separate `scripts/collect_court_trace.py`, NOT generalized |
| W-TinyLFU structure | Header-only C++17, zero external deps, roll-your-own CMS + Doorkeeper |
| W-TinyLFU config | 1% window LRU / 99% main SLRU (80/20 protected/probation); CMS depth=4, width=nextpow2(capacity-objects), counters=4-bit, aging halve every 10×W, conservative update |
| Hill-climbing | **Omit.** Fixed static config. |
| Doorkeeper | **Optional first-cut.** Ship without; add later as second pass. |
| Hash | FNV-1a extracted to `include/hash_util.h` with 4 deterministic seeds. `std::hash` banned. |
| W-TinyLFU seed | `uint64_t seed` constructor param, default committed (e.g., 42) |
| Simulator model | Single trace per invocation; cross-workload done in Python |
| Results layout | `results/{congress,court,shards_large,compare}/` |
| SHARDS 1M validation | Self-convergence across 0.01%/0.1%/1%/10% sampling, NOT vs exact oracle |
| Exact MRC | Keep on ≤50K trace (existing guard) |
| `replay_zipf` refactor | Accept prepared shuffled-object list BEFORE 84-run final sweep |
| Throughput metric | NEW `accesses/sec` column — add early |

---

## Critical Risks (top 5 to address early)

1. **C1 PACER vs CourtListener** — $500-$2,000 cost, SSN/EIN gate. Mitigate: host-allowlist `www.courtlistener.com` only; lock the target before writing HTTP code.
2. **C2/C3 rate limits + gated endpoints** — silently stretches a day to a week. Mitigate: `curl`-verify token before 20K run; unauthenticated-test each planned endpoint; bake in `Retry-After` honor + 5-consecutive-429 abort.
3. **C5 CMS without aging** — breaks W-TinyLFU plausibly but wrong. Mitigate: periodic halving + conservative update from day one. Signal: hit rate bumping and then *declining* during warmup = aging broken.
4. **C6 W-TinyLFU admission edge cases** — ±2-5% miss-ratio error looks like "different workload." Mitigate: static config, mirror `com.github.benmanes.caffeine.cache.simulator.policy.sketch.WindowTinyLfuPolicy` line-by-line; write "20-access hot object survives 1000-access scan" unit test BEFORE real traces; W-TinyLFU must beat LRU at every cache size on Zipf(0.8).
5. **C7 live demo failure** — DYLD/X-forwarding/sleep/slow I/O. Mitigate: `demo.sh` tested on target laptop; <10K pre-loaded trace; pre-rendered figures; screen-recording backup; disable sleep.

---

## Suggested Build Order

**Phase 1 — Enabling refactors + trace-source verification (parallel):**
- Track A (C++): Extract FNV-1a → `include/hash_util.h`. Refactor `replay_zipf` to accept prepared shuffled object list. Add throughput (`accesses/sec`) measurement.
- Track B (Python): Register CourtListener account, obtain token, verify with curl. Pilot 200-request run across 4 planned endpoints; tune ID upper bounds to ≥70% success.
- Pre-work verification: pull Caffeine `FrequencySketch.java`, confirm `sampleSize = 10×max(capacity,1)` and halving formula before locking W-TinyLFU numbers.

**Phase 2 — Core new work (parallel):**
- Track A (C++): Count-Min Sketch → W-TinyLFU (static config, NO Doorkeeper yet). Integrate into `cache.h` + `make_policy()`. Validate on Congress: W-TinyLFU > LRU at α≥0.8; within ±2% of LRU at α=0.
- Track B (Python): CourtListener collector script; long-running ≥20K-request collection.

**Phase 3 — Analysis infrastructure:**
- `scripts/compare_workloads.py`.
- 1M-synthetic SHARDS run with extended sampling-rate vector (add 0.0001 and 0.01).
- Doorkeeper (optional) + with/without ablation figure.
- Full alpha sweep on both traces × 5 seeds (2×6×7×5 = 420 runs; cheap with the `replay_zipf` refactor).

**Phase 4 — Writeup + demo:**
- Final report (≥1 week budget): workload char table up front, "winner per regime" + practitioner decision tree at end.
- AI-use report: include the PROCESS.md 9-bug list as concrete learning moments (more credible than successes-only).
- `demo.sh`, 3+ dry-runs on target laptop, screen-recording backup, <60s total runtime.

**Cut-if-runway-tightens (in priority order):** Doorkeeper ablation → S3-FIFO ratio sweep → learning curves → multi-seed CIs. Do NOT cut: W-TinyLFU core, CourtListener trace, SHARDS 1M self-convergence, AI-use report, throughput infrastructure.

---

## Open Questions (need user input at planning)

1. Doorkeeper in or out for the first cut? (Recommendation: omit initially, add later.)
2. Court opinion scope: full `plain_text` (realistic tail, triggers rate limits sooner) vs metadata-only via field selection? (Either defensible; must be explicit in report.)
3. Caffeine trace cross-validation stretch goal (run our W-TinyLFU on a Caffeine-published trace for ±2% validation)? ~1 day optional.
4. Ablation scope: only W-TinyLFU ± Doorkeeper, or also S3-FIFO small-ratio sweep and SIEVE visited-bit-off?
5. Multi-seed count: 3, 5, or 10? (Recommend 5.)
6. Cache-size sweep granularity: keep 4-5 discrete points or expand to ≥20 log-scale for MRC figures?

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack — deps (Python/C++) | HIGH | No additions; all std headers already included |
| Stack — CourtListener API | HIGH | Context7-verified |
| Stack — W-TinyLFU structure | HIGH | Caffeine wiki Context7-verified |
| Stack — W-TinyLFU exact constants | MEDIUM | Verify against Caffeine `FrequencySketch.java` in Phase 1 |
| Features — cache-paper figures | MEDIUM | Training-data recollection; verify before final-report citations |
| Features — demo best practices | HIGH | Standard + confirmed by community knowledge |
| Architecture — existing codebase | HIGH | Directly read all referenced files |
| Architecture — extension points | HIGH | Documented in `codebase/STRUCTURE.md` |
| Pitfalls — API / rate-limit / W-TinyLFU correctness | HIGH | Context7 + Caffeine wiki |
| Pitfalls — writeup / demo | MEDIUM | Community-wisdom synthesis; conservative recommendations |

**Overall: HIGH confidence.** The one MEDIUM zone (exact W-TinyLFU constants) has a bounded verification action that fits in Phase 1.

---

## Sources

**Context7-verified (HIGH):**
- `/websites/courtlistener_help_api_rest` — base URL, token auth, 5k/hr, 10-min query cache, 429 semantics, cursor pagination
- `/websites/courtlistener_help_api_rest_pacer` — gated endpoints, `plain_text` field selection, court-ID mapping
- `/ben-manes/caffeine` wiki — 4-bit CMS, 8 bytes/element, HashDoS jitter, hill-climbing option

**Training-data (MEDIUM — verify before locking):**
- Einziger-Friedman-Manes 2017 TOS (TinyLFU paper)
- Caffeine `FrequencySketch.java` + `BoundedLocalCache.java` (to be read in Phase 1)
- Zhang et al. NSDI '24 (SIEVE); Yang et al. SOSP '23 (S3-FIFO); Beckmann et al. NSDI '18 (LHD); Waldspurger et al. FAST '15 (SHARDS); Berger et al. NSDI '17 (AdaptSize)

**Internal (HIGH — directly read):**
- `include/cache.h`, `src/main.cpp`, `src/shards.cpp`, `scripts/collect_trace.py`
- `.planning/PROJECT.md`, `.planning/codebase/ARCHITECTURE.md`, `STRUCTURE.md`, `CONCERNS.md`
- `PROCESS.md` (9-bug history for AI-use report), `progress.tex`
