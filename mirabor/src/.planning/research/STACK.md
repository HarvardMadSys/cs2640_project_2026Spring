# Technology Stack — Milestone 2 Extensions

**Project:** civicache (CS 2640 Final Project)
**Scope:** Court-records trace collector (Python 3.13) and W-TinyLFU cache policy (header-only C++17)
**Researched:** 2026-04-16
**Overall confidence:** HIGH for CourtListener API surface (Context7-verified from official docs); HIGH for W-TinyLFU structure (Caffeine wiki Context7-verified); MEDIUM for exact numeric parameters (specific constants like sample-size multiplier come from Caffeine reference and original paper via training-data, not directly verified in Context7 fetches — verify against the Caffeine source link in the Sources section before locking numbers).

---

## Languages

**Existing, reused as-is:**
- **C++17** — simulator core. `-std=c++17` flag in `Makefile`. W-TinyLFU additions go in `include/` as header-only classes to match the existing `CachePolicy` pattern in `include/cache.h:34`.
- **Python 3.13** — trace collection tooling. Existing `.venv13/` indicates 3.13 is the development interpreter. Only standard-library features plus `requests` are needed (no 3.13-specific syntax required; code should stay 3.10+ compatible for portability).

**No new language additions.** Do NOT introduce Rust, Zig, Go, or C++20 for these extensions — adds build complexity without benefit given the existing Makefile + pip workflow is working.

---

## Frameworks

### Court-records collector (Python)

**Primary:**
- **`requests` ≥ 2.32** — already in `requirements.txt`, already exercised in `scripts/collect_trace.py` against Congress.gov. No change needed.
- **Python stdlib** (`argparse`, `csv`, `os`, `random`, `sys`, `time`) — same set used by the existing collector.

**Not using and why:**
- `httpx` — would duplicate functionality of `requests` with no benefit for a single-threaded, rate-limited collector.
- `aiohttp` / `asyncio` — the 5,000 req/hour cap (≈ 1.4 req/s) means async offers zero throughput gain; adds complexity.
- `juriscraper` (Free Law Project's scraping library) — designed for scraping court websites, not for consuming the CourtListener API. Overkill and off-target.
- CourtListener-specific Python SDKs — none are officially endorsed by Free Law Project; community SDKs are sparse and stale. Hand-rolled HTTP against a well-documented REST API is simpler and matches the existing Congress collector's style.

### W-TinyLFU policy (C++17)

**Primary:**
- **C++17 standard library only.** New headers use `<array>`, `<vector>`, `<cstdint>`, `<random>`, `<string>`, `<functional>` (for `std::hash`), `<list>`, and `<unordered_map>` — all already pulled in by existing policies.
- **Header-only pattern** matching `include/cache.h`. New files:
  - `include/count_min_sketch.h` — CMS frequency estimator with 4-bit counters and periodic aging.
  - `include/wtinylfu.h` — W-TinyLFU cache policy (1% window LRU + 99% SLRU main region + TinyLFU admission + optional Doorkeeper).
  - Optional: `include/doorkeeper.h` — single-use Bloom filter in front of the CMS.

**Not using and why:**
- **Boost** — would bring `boost::hash_combine`, `boost::dynamic_bitset`, `boost::circular_buffer`. Violates the project's zero-external-dep rule; `std::hash<std::string>` plus a multiplicative mix suffices.
- **Abseil** — same reasoning. `absl::flat_hash_map` would be marginally faster than `std::unordered_map` but this is a simulator, not production.
- **Existing open-source C++ CMS libs** (e.g., `cm-sketch` on GitHub, `CountMinSketch` in various cache libs) — most are GPL or non-permissive, sparsely maintained, or depend on Boost. The CMS algorithm is <150 lines; rolling our own is faster than vetting licenses.
- **Caffeine's FrequencySketch Java port** — the block-packed 64-bit-word trick (four 4-bit counters per word) is clever but over-optimized for a simulator. A straightforward 2D `uint8_t` table with 4-bit packing into bytes is easier to validate against published results.
- **`xxhash` / `cityhash` / `wyhash`** — third-party hashers. `std::hash<std::string>` combined with two multiplicative mix functions (`splitmix64`-style) gives enough independence for a 4-row sketch and keeps the dependency list empty. Caffeine's sketch uses a single 64-bit hash split into multiple "hash" values via block addressing; we replicate this pattern.

---

## Libraries

### Python (court-records collector)

**Declared in `requirements.txt`, no additions needed:**

| Library | Version | Purpose | Notes |
|---------|---------|---------|-------|
| `requests` | ≥ 2.32 | HTTP client for CourtListener v4 | Reuse `requests.Session()` with default header `Authorization: Token <token>` and query param `format=json`. Mirrors `scripts/collect_trace.py:116-117`. |

No new entries to `requirements.txt`. `scipy` remains listed-but-unused (pre-existing; not our concern for this milestone).

### C++ (W-TinyLFU policy)

**All `<...>` standard headers — no additions to the build system:**

| Header | Purpose | Used by |
|--------|---------|---------|
| `<array>` | Fixed-size rows of the CMS (sketch depth = 4) | `count_min_sketch.h` |
| `<vector>` | Row-width storage, resizable by constructor argument | `count_min_sketch.h`, `doorkeeper.h` |
| `<cstdint>` | `uint64_t`, `uint32_t`, `uint8_t` for packed counters | all new headers |
| `<functional>` | `std::hash<std::string>` as the base hash | `count_min_sketch.h`, `doorkeeper.h` |
| `<list>` + `<unordered_map>` | Window-LRU and SLRU segments (same pattern as `LRUCache`) | `wtinylfu.h` |
| `<random>` | Jitter for hash-DoS mitigation (optional; Caffeine does this) | `wtinylfu.h` |

No link-time additions to `Makefile`. The existing `-MMD -MP` dependency tracking picks up new headers automatically because they are `#include`'d from `src/main.cpp`.

---

## External Services

### Court records API: CourtListener v4 (recommended, HIGH confidence)

**Decision: Use CourtListener REST API v4 via the `/api/rest/v4/` base. Do NOT use PACER directly.**

**Rationale (evidence-based):**

1. **Access model.** PACER requires a per-user account tied to a court registration, charges $0.10/page (capped at $3.00 per document), and explicitly forbids automated scraping outside the RECAP protocol. A 20K-request trace against PACER would cost on the order of $600–$2,000 depending on page counts, plus registration friction. CourtListener's REST API is free to authenticated users (Context7, `/websites/courtlistener_help_api_rest`, "Rate Limits API Documentation" snippet: "The CourtListener API allows up to 5,000 queries per hour for authenticated users").
2. **Coverage.** CourtListener is the Free Law Project's open mirror of PACER + case-law + oral arguments, populated by the RECAP browser extension plus bulk ingestion. For the cache-simulator use case we need metadata and response bodies, not paid PDF downloads — CourtListener serves this for free.
3. **Trace-collection symmetry.** Congress.gov and CourtListener both expose deterministic resource URLs (`/bill/{congress}/{type}/{number}` vs `/api/rest/v4/dockets/{id}/`) that we can sample pseudo-randomly and weight toward a popular sub-domain (current Congress ↔ recent dockets). Same replay-Zipf strategy, same CSV output shape.
4. **Contrast with Congress.** Court records have notably larger response bodies (docket JSON includes nested parties/attorneys metadata; opinion clusters include full text when requested). This gives the cross-workload story a real size-distribution contrast, which the project's "Key Decisions" section in `PROJECT.md` specifically calls out as motivation.

**API surface (Context7-verified from official docs):**

| Endpoint | Method | Purpose | Request-body size (rough) |
|----------|--------|---------|---------------------------|
| `GET /api/rest/v4/dockets/{id}/` | single-resource fetch | Individual docket metadata | ~2–8 KB |
| `GET /api/rest/v4/dockets/?court={court_id}` | list | Browse dockets by court | ~20 results/page, ~40–150 KB |
| `GET /api/rest/v4/opinions/{id}/` | single-resource fetch | Individual opinion record | ~5–50 KB (larger when `plain_text` included) |
| `GET /api/rest/v4/clusters/{id}/` | single-resource fetch | Opinion cluster (case + sub-opinions) | ~5–30 KB |
| `GET /api/rest/v4/courts/{court_id}/` | single-resource fetch | Court metadata (stable, cache-friendly) | ~1 KB |
| `GET /api/rest/v4/search/?type=o&q=...` | search | ElasticSearch-backed; lower priority for our use | variable |

**Do NOT use:**
- `POST /api/rest/v4/recap-fetch/` — this is the paid PACER proxy that charges PACER fees to purchase documents not already in RECAP. For a trace collector that generates ≥20K requests, this would be expensive and semantically wrong (it's a write/purchase endpoint, not a read endpoint).
- `/api/rest/v4/docket-entries/` and `/api/rest/v4/recap-query/` — Context7 docs flag these as "only available to select users" (gated endpoints). Prefer the freely available resources.

**Authentication:**
- HTTP header `Authorization: Token <your-token-here>` (Context7, `/websites/courtlistener_help_api_rest_pacer`, multiple snippets).
- Token obtained by creating a free account at `https://www.courtlistener.com/sign-up/` and copying the token from the profile page.
- Store in `COURTLISTENER_API_KEY` env var, parallel to `CONGRESS_API_KEY`. Add the name to `.env` (user exports manually; `.env` is already gitignored per existing setup in `INTEGRATIONS.md`).

**Rate limiting (Context7-verified, HIGH confidence):**
- **5,000 queries per hour** for authenticated users. This is a flat account-wide cap, not per-endpoint (contrast with the deprecated v2 at 1,000/endpoint/hour).
- Recommended client pacing: **0.8 s base delay + 0–0.4 s jitter** (≈ 1.1 s mean per request → ~3,200 req/hour, leaving ~36% headroom). This is faster than the Congress.gov 1.2 s base because CourtListener's cap is ~5× looser than Congress's 1 req/s.
- On HTTP 429 → exponential backoff `min(base_delay * 2^n, 300)`, same formula as `scripts/collect_trace.py:139`. Reuse the existing structure verbatim.
- On HTTP 5xx → same backoff.
- On 404/403 → skip without writing to trace (same as existing code at `scripts/collect_trace.py:146-147`). 404s are expected because we generate docket IDs probabilistically and not every ID exists.

**Pagination (Context7-verified):**
- Default 20 results/page (snippet: "This can be used to fetch all entries for a particular case. The response is paginated, with a default of 20 entries per page.").
- `page=` GET parameter is capped at 100 pages for most endpoints.
- For deep traversal, use cursor-based pagination via the `next`/`previous` keys (returned when ordering by `id`, `date_modified`, or `date_created`).
- For trace collection we do NOT paginate — we generate random resource IDs and hit detail endpoints directly, matching the Congress collector's pattern.

**Trace-generation strategy (mirrors Congress approach):**

```python
ENDPOINTS = {
    "docket":     {"path": "/api/rest/v4/dockets/{id}/",     "id_range": (1, 80_000_000)},
    "opinion":    {"path": "/api/rest/v4/opinions/{id}/",    "id_range": (1,  15_000_000)},
    "cluster":    {"path": "/api/rest/v4/clusters/{id}/",    "id_range": (1,  12_000_000)},
    "court":      {"path": "/api/rest/v4/courts/{court_id}/","court_ids": [...static list of ~3000 court IDs...]},
}
# Weights: 60% docket, 25% opinion, 10% cluster, 5% court (court is hot-cached; provides repeat hits)
```

ID ranges are approximate upper bounds for CourtListener's current corpus; 404 rate will be non-trivial at the high end of each range (acceptable — skipped per existing logic). **Tune down the upper bounds after a pilot run of 200 requests** to keep the 404 rate below ~30%.

### PACER direct — explicitly rejected

- Registration at `https://pacer.uscourts.gov/register-account` requires a credit card on file.
- Per-page fees ($0.10/page, $3.00/doc cap); automated access is restricted to the documented RECAP APIs or a paid NextGen CM/ECF integration.
- The PACER Service Center's "PACER Case Locator" search API (the closest thing PACER has to a read-only API) is IP-allowlisted for court officers, not open to the public.
- **Not a viable source for a student project with a free-tier budget.** Use CourtListener; this is exactly the use case RECAP/Free Law Project was built for.

---

## W-TinyLFU Implementation Parameters

**Reference implementation:** Caffeine's `FrequencySketch.java` and `BoundedLocalCache.java` (linked from Caffeine wiki, Context7-verified as the Java/JVM canonical W-TinyLFU). Paper: Einziger, Friedman, and Manes, "TinyLFU: A Highly Efficient Cache Admission Policy" (TOS 2017).

### Count-Min Sketch (frequency estimator)

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| **Counter width** | 4 bits (max value 15) | Caffeine standard. Saturates at 15, which is plenty — the sketch is compared, not read precisely. (Context7, Caffeine wiki: "Frequencies are stored in CountMinSketch using 4 bits per element, costing 8 bytes per element for frequency calculation.") |
| **Depth (rows)** | 4 | Standard CMS parameter giving ≈ 2^-4 = 6.25% collision probability per row; aggregate min-of-4 makes overcount unlikely. Caffeine uses 4. |
| **Width (columns)** | next-power-of-2 ≥ `cache_size_in_objects` | Caffeine's default. For our simulator's configurable cache sizes the width gets sized once per experiment from the cache capacity argument. |
| **Hash functions** | `std::hash<std::string>` + 4 different multiplicative seeds (`0xc3a5c85c97cb3127ULL`, `0xb492b66fbe98f273ULL`, `0x9ae16a3b2f90404fULL`, `0xcbf29ce484222325ULL`) | Reuses Caffeine's FNV/SplitMix-style seeds. Four pseudo-independent hashes from one base hash — bypasses needing 4 separate hash implementations. |
| **Aging (decay) policy** | After every `10 × W` accesses (where `W` = sketch sample size = width × depth), halve every counter (shift right by 1) | Einziger & Friedman "aging" rule. Prevents permanent dominance of early-hot keys. Caffeine triggers aging when a global counter equals `sample_size = 10 * max(capacity, 1)`; we mirror exactly. |
| **Memory per sketch** | `depth × width × 0.5 bytes` (4-bit packed) | For a 50K-object cache → width ≈ 65536 → 128 KB per sketch. Acceptable. |

### Doorkeeper (Bloom filter in front of CMS)

The paper describes TinyLFU-with-Doorkeeper as a first-time filter: a key must appear in the Doorkeeper bloom filter before its count is recorded in the CMS. This cuts the CMS pressure from one-hit-wonders by roughly 50–70% for typical workloads.

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| **Bloom filter size (bits)** | `cache_size_in_objects × 4` | Paper recommends ~4 bits/element to hit ≈ 13% FPR. Enough for the Doorkeeper's "first-time filter" role — false positives just mean one extra element gets recorded in the CMS, which is cheap. |
| **Hash functions** | 2 (double-hashing: `h1(x) + i * h2(x)`) | Standard Kirsch-Mitzenmacher trick; avoids computing k separate hashes. |
| **Reset policy** | Halved/cleared on the same schedule as the CMS aging (every `10 × W` accesses) | Keeps the Doorkeeper from accumulating stale bits; aligned with the CMS so freshness is consistent. |

**Optional for the first cut.** The simulator can ship without the Doorkeeper and still be called "W-TinyLFU." If time is tight, implement W-TinyLFU = Window LRU + Segmented LRU + CMS admission first, then add the Doorkeeper as a second pass — it's a prefilter and can be introduced without changing the admission interface.

### Cache regions

| Region | Size | Structure | Notes |
|--------|------|-----------|-------|
| **Window** | 1% of total capacity | LRU | Captures recency; every new admission enters here first. Caffeine defaults to 1% and uses hill-climbing to tune; we use a fixed 1% for reproducibility in an academic eval. |
| **Main** | 99% of total capacity | SLRU (Segmented LRU) | Split into **Protected 80% / Probationary 20%** per Caffeine defaults. Keys evicted from Window go to the Probationary segment if they win the TinyLFU admission contest vs. the Probationary victim; on second hit they promote to Protected. |
| **Admission test** | `freq(candidate) > freq(victim)` in the CMS | Tie-breaker: reject candidate (Caffeine behavior; mildly favors incumbents) | Per Einziger & Friedman 2017 §4.2. |

**Do NOT implement the hill-climbing adaptive window-size tuner from Caffeine.** It's orthogonal to the admission-policy story, adds ~200 LoC of hill-climb logic, and complicates comparison with the other policies. Fixed 1/99 split with fixed 80/20 SLRU is the canonical "W-TinyLFU baseline" most papers benchmark against.

### Integration with existing simulator

- Subclass `CachePolicy` (base class at `include/cache.h:34`) exactly like `LRUCache`, `FIFOCache`, etc.
- Implement `bool access(const std::string& key, uint64_t size) override` returning hit/miss and calling `record(hit, size)`.
- `name()` returns `"W-TinyLFU"`.
- `reset()` clears window, protected, probationary lists, CMS table, and Doorkeeper bitset, and zeros `stats`.
- Add to the policy factory in `src/main.cpp` where the existing five policies are instantiated (`src/main.cpp:167-199` for the MRC loop — new policy string `"wtinylfu"` in the `--policies` CLI flag).

---

## Tooling

### Existing (unchanged)
- **GNU Make** — `Makefile` picks up new headers via `-MMD -MP`.
- **g++** / **clang++** with `-std=c++17 -O2 -Wall -Wextra`.
- **pip + requirements.txt** for Python deps.

### Validation approach (no new tools required)

For W-TinyLFU correctness, add a self-test in `src/main.cpp` (or a new `src/wtinylfu_test.cpp` target):

1. **CMS unit check:** insert key `A` 100 times, key `B` once; assert `freq(A) > freq(B)` after insertion. Assert `freq(A) >= 15` is possible (saturation).
2. **Aging check:** insert `A` 100 times, trigger aging, assert `freq(A) <= 50` (halved).
3. **Admission regression:** feed a small known-hot-key trace (1K-access synthetic Zipf-0.9, 100 unique keys, cache size 10) and verify hit ratio matches a hand-computed expected value (within ±2%).
4. **Cross-check vs. Caffeine:** run the same workload through the Caffeine simulator's `WindowTinyLfuPolicy` (Java one-shot from the Caffeine simulator jar) and confirm hit ratios match within 3% — that tolerance absorbs differences in hash functions and jitter.

No new testing framework. Use assertions and `std::cerr` reporting, consistent with the existing project's "no formal test suite" posture documented in `codebase/STACK.md:36-37`.

---

## Configuration

### New environment variable

| Name | Required by | Purpose | Where read |
|------|-------------|---------|------------|
| `COURTLISTENER_API_KEY` | Court-records collector only | Bearer token for CourtListener v4 API | New `scripts/collect_court_trace.py`, via `os.environ.get("COURTLISTENER_API_KEY")`; exits with `Error: set COURTLISTENER_API_KEY environment variable` if unset (mirror pattern from `collect_trace.py:53`). |

Add to the project `.env` (gitignored) alongside `CONGRESS_API_KEY`. Update `README.md:64`-area export instructions in a subsequent milestone.

### New CLI flags (C++ simulator)

Extend `src/main.cpp` argument parser to accept `wtinylfu` in the `--policies` comma-separated list (existing flag, no new flag needed). Optional new flags for W-TinyLFU tuning (low priority, default-only is fine for first cut):

- `--wtinylfu-window-frac <f>` — default `0.01`
- `--wtinylfu-main-protected-frac <f>` — default `0.80`
- `--wtinylfu-cms-depth <n>` — default `4`

### New CLI flags (Python collector)

The new `scripts/collect_court_trace.py` takes the same flags as `scripts/collect_trace.py` plus:

- `--base-delay <s>` — default `0.8` (vs Congress's effective 1.2)

---

## Rationale Summary

### Court records → CourtListener v4 (HIGH confidence)

| Criterion | CourtListener v4 | PACER direct | Winner |
|-----------|------------------|--------------|--------|
| Cost | Free (API is free; bulk data is free) | $0.10/page, ≥$600 for a 20K trace | CourtListener |
| Rate limit | 5,000 req/hour authenticated | Undocumented + scraping restrictions + legal exposure | CourtListener |
| Auth | Free account + token header | Paid account, client agreement | CourtListener |
| API surface | Modern REST v4, JSON, cursor pagination, OPTIONS for schema discovery | No public read API (PACER Case Locator is IP-allowlisted) | CourtListener |
| Symmetry with Congress collector | `GET /resource/{id}/` pattern matches Congress's `/bill/{congress}/{type}/{num}/` | N/A | CourtListener |
| Size-distribution contrast vs Congress bills | Docket JSON (~2–50 KB) vs bill JSON (~5–15 KB); opinion plaintext (~50+ KB) — good spread | N/A | CourtListener |

### W-TinyLFU implementation → Header-only C++17, roll-your-own CMS + Bloom (HIGH confidence on structure, MEDIUM on constants)

| Criterion | Approach | Winner |
|-----------|----------|--------|
| External deps | Zero (matches project's existing posture) | Roll-your-own |
| Validation | Cross-check against Caffeine simulator's `WindowTinyLfuPolicy` | OK |
| Complexity | ~400 LoC total across CMS (~100), Doorkeeper (~60 optional), W-TinyLFU class (~250) | Manageable within milestone budget |
| Parameter authority | Caffeine defaults = de facto W-TinyLFU reference; paper validates the algorithm | HIGH confidence on 1%/99%, 80/20, depth=4, 4-bit counters; MEDIUM on exact aging-trigger multiplier (10×W per training data and Caffeine reading) — confirm against `FrequencySketch.java` before locking |

---

## Installation / Setup Delta

No new packages. Existing setup suffices:

```bash
# Python (unchanged)
pip install -r requirements.txt  # requests, matplotlib, numpy, pandas, scipy

# C++ (unchanged)
make            # builds cache_sim; picks up new include/ headers automatically

# New env var
export COURTLISTENER_API_KEY=<token-from-courtlistener.com/profile>
export CONGRESS_API_KEY=<existing-token>
```

---

## Sources

### Context7-verified (HIGH confidence)
- CourtListener REST API overview: `/websites/courtlistener_help_api_rest` — base URL `https://www.courtlistener.com/api/rest/v4/`, Token auth header, 5000 req/hr rate limit, 429 throttling semantics, pagination defaults.
- CourtListener PACER data endpoints: `/websites/courtlistener_help_api_rest_pacer` — `/dockets/{id}/`, `/parties/?docket=`, `/docket-entries/?docket=` (gated), `/recap-query/` (gated), `/recap-fetch/` (paid proxy).
- Caffeine W-TinyLFU architecture: `/ben-manes/caffeine` wiki — 4-bit Count-Min Sketch, 8 bytes/element overhead, Window + main SLRU, hill-climbing adaptive sizing (which we deliberately omit), HashDoS mitigation.

### Training-data (MEDIUM confidence, verify before locking)
- Einziger, Friedman, and Manes, "TinyLFU: A Highly Efficient Cache Admission Policy," ACM Transactions on Storage, 2017 — specific aging-period multiplier (10×W), Doorkeeper bit budget (4 bits/element), SLRU 80/20 split.
- Caffeine `FrequencySketch.java` and `BoundedLocalCache.java` — implementation specifics for block-packed 4-bit counters (we intentionally use a simpler unpacked representation).

### Verification action items before implementation
1. Pull `https://github.com/ben-manes/caffeine/blob/master/caffeine/src/main/java/com/github/benmanes/caffeine/cache/FrequencySketch.java` and read the `sampleSize` and `reset()` method to confirm the "10×W" aging trigger and the exact halving formula.
2. Pilot-run 200 CourtListener requests at the proposed ID ranges; measure 404 rate and adjust upper bounds to keep ≥70% success.
3. Confirm CourtListener account creation and token issuance works (expected 1 business day max) before committing to this path in the roadmap.

---

*Stack research: 2026-04-16*
