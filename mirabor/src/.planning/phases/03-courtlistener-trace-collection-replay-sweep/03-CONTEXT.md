# Phase 3: CourtListener Trace Collection & Replay Sweep — Context

**Gathered:** 2026-04-19
**Status:** Ready for planning

<domain>
## Phase Boundary

A real ≥20K-request CourtListener trace on disk at `traces/court_trace.csv`, plus a full 6-policy replay-Zipf sweep run on it, producing `results/court/{mrc.csv, alpha_sensitivity.csv, workload_stats.json, figures/, collection_report.txt}`. Both artifacts (trace + sweep outputs) are prerequisites for Phase 5's cross-workload comparison.

What's explicitly IN scope:
1. `scripts/collect_court_trace.py` — production collector (25% equal mix across dockets/opinions/clusters/courts; 80/20 metadata-vs-full `plain_text` mix on opinions via `?fields=`; resume support; 5-consecutive-429 hard-stop)
2. `traces/court_trace.csv` — 20K successful `(timestamp, key, size)` rows, committed to git
3. `results/court/workload_stats.json` — lightweight pre-sweep characterization (mean/median/p95/max size, unique objects, natural α MLE, OHW ratio)
4. `results/court/collection_report.txt` — per-endpoint success tally (200s / 404s / 403s / 429s / other)
5. 6-policy replay-Zipf sweep over `traces/court_trace.csv`: LRU, FIFO, CLOCK, S3-FIFO, SIEVE, W-TinyLFU producing `results/court/{mrc.csv, alpha_sensitivity.csv}` plus regenerated figures via `make plots --workload court`

What's explicitly NOT in scope:
- `scripts/compare_workloads.py` and the side-by-side Congress vs Court table — Phase 5 (ANAL-01, ANAL-03)
- Multi-seed confidence-interval runs — Phase 5 (ANAL-02)
- SHARDS on the court trace — Phase 4 uses a 1M synthetic trace, not court (SHARDS-01..03)
- Doorkeeper ablation on court — Phase 4 (DOOR-03)
- Any refactor to `src/main.cpp` sweep parameters — the existing α {0.6..1.2} × cache-fraction grid is reused verbatim

</domain>

<decisions>
## Implementation Decisions

### Opinion plain_text mix (ROADMAP "80% metadata / 20% full plain_text")

- **D-01:** **The 80/20 mix is implemented via `?fields=` on opinion requests.** 80% of `/opinions/{id}/` calls pass `?fields=<minimal>` to strip `plain_text` (and related body fields); 20% fetch the full object by omitting `?fields=`. Per-request random draw, seeded for determinism.
- **D-02:** **Minimal field set for the 80% metadata calls:** `id, absolute_url, type, date_filed, author_id` (or nearest v4-schema equivalents — confirm field names from CourtListener docs during planning). Target response size ~1–5 KB. Clean contrast against the 20% full-fetch range (~20–500 KB when `plain_text` is populated).
- **D-03:** **80/20 applies only to `/opinions/`.** Dockets (`/dockets/{id}/`), clusters (`/clusters/{id}/`), and courts (`/courts/{court_id}/`) always fetch the default response (no `?fields=` parameter). Matches the literal ROADMAP spec and keeps the collector's control surface narrow.
- **D-04:** **CSV `size` column = actual response byte length (`len(response.content)`), regardless of content.** If a 20%-full opinion happens to have `plain_text=null` the row still records the true response size (~1–5 KB for a bare metadata object) — do NOT retry or re-roll the ID. Matches pilot behavior and keeps the collector single-shot per draw.

### Endpoint distribution in the 20K

- **D-05:** **Equal 25% per endpoint:** 5,000 successful rows each for dockets, opinions, clusters, courts. Predictable workload characterization and clean symmetry with the pilot's 50/50/50/50 pattern.
- **D-06:** **Keep pilot's ID ranges for production.** Pilot success rates (dockets 90%, opinions 74%, clusters 90%, courts 100%) are acceptable for 20K — 404s are skipped per D-10 and do not pollute the trace. **Fallback rule:** if the first 500 issued requests of any endpoint show <60% success, narrow that endpoint's ID range by 33% (upper bound × 0.67) and restart that endpoint's counter. Log the adjustment in `collection_report.txt`.

### Collector runtime + trace semantics

- **D-07:** **20K target means 20K successful rows in the CSV.** Collector keeps issuing requests until `wc -l traces/court_trace.csv` ≥ 20,001 (header + 20K data rows). Expected ~24K issued requests at ~85% average success; runtime ~6.1 hours at 0.8s + 0–0.4s jitter per request.
- **D-08:** **CSV written append-per-row with `flush()` after every successful request.** Matches `scripts/collect_trace.py` pattern (the Congress template). Allows resume on crash without losing in-flight work.
- **D-09:** **`--resume` flag** reads the existing `traces/court_trace.csv`, counts successful rows per endpoint, computes remaining per-endpoint targets to hit the 25/25/25/25 split, and continues the request loop with fresh random IDs. Without `--resume`, the collector refuses to overwrite an existing non-empty CSV (prints error, exits non-zero).
- **D-10:** **Failed requests (404, 403, 429, network errors) are skipped from the trace CSV.** Matches Phase 1 D-08 ("success = HTTP 200 + size > 0") and the Congress trace semantics. Failed requests ARE counted in the per-endpoint tally written to `results/court/collection_report.txt` (200s / 404s / 403s / 429s / other / total / success-percent per endpoint).
- **D-11:** **429 handling — Retry-After + exponential ramp on consecutive 429s.** First 429: `sleep(min(Retry-After, 60))` then retry the same URL once. Second consecutive 429: `sleep(Retry-After + 30)`. Third: `sleep(Retry-After + 90)`. After 5 consecutive 429s within any endpoint's loop, **hard-stop the collector** with a one-line diagnostic: `FATAL: 5 consecutive 429s — token throttled or pacing too aggressive. Resume after 1 hour.` Exits non-zero. The `--resume` flag from D-09 allows continuation after the throttle window clears.

### Sweep parameters + pre-characterization

- **D-12:** **Sweep grid is identical to Congress.** Reuse `src/main.cpp`'s hardcoded α grid `{0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2}` and the cache-fraction grid (currently `{0.001, 0.005, 0.01, 0.02, 0.05, 0.1}` per `src/main.cpp`). Sweep command: `./cache_sim --trace traces/court_trace.csv --replay-zipf --alpha-sweep --policies lru,fifo,clock,s3fifo,sieve,wtinylfu --output-dir results/court/` (or equivalently, `make run-sweep --workload court` after updating that Makefile target). Zero simulator changes — direct cross-workload comparability for Phase 5.
- **D-13:** **Workload pre-characterization runs in Phase 3, lightweight.** After collection completes, invoke `./cache_sim --trace traces/court_trace.csv --workload-stats-only --output-dir results/court/` to write `results/court/workload_stats.json` with mean/median/p95/max size, unique-object count, natural α MLE, and OHW-ratio. Mirrors what Phase 1 produced for Congress via `src/workload_stats.cpp`. Runs in seconds. Catches PITFALLS M5 size-distribution surprises BEFORE the ~10-minute sweep.
- **D-14:** **Output layout under `results/court/` mirrors Congress exactly.** Files: `mrc.csv`, `alpha_sensitivity.csv`, `workload_stats.json`, `figures/*.pdf`, `collection_report.txt`. No novel artifacts, no manifest file — Phase 1 D-04/D-05 precedent governs.

### Trace commit policy

- **D-15:** **`traces/court_trace.csv` is committed to git.** Size estimate: 20K rows × ~60 bytes = ~1.2 MB (same order as Congress's 1.6 MB committed `traces/congress_trace.csv`). Commit locks the dataset against CourtListener API drift (ID numbering may shift as RECAP ingests new filings), preserves reproducibility for the grader, and matches Phase 1 precedent. `results/court/*.csv` stays gitignored (generated; local only per `.gitignore`).

### Claude's Discretion

- Exact v4 field-name strings for the D-02 minimal-fields set — verify against CourtListener docs during planning; substitute any aliases or dropped fields with nearest equivalents.
- Specific per-endpoint random-seed handling inside the collector — just pick `random.seed(42)` or equivalent so two `--resume` runs on the same partial CSV produce identical ID draws for the remaining rows. No deep seeding protocol needed.
- Logging verbosity during the 6-hour collection — mirror `scripts/collect_trace.py`'s print pattern (every 100 rows, endpoint tally every 1000 rows). Not worth a discussion cycle.
- How the Makefile exposes `run-sweep --workload court` if the current target is Congress-only — add the flag, a wave-specific sub-target, or a second top-level target; whichever is cleanest.
- How to stamp the `collection_report.txt` — plain text with header-summary-per-endpoint format from `scripts/pilot_court_trace.py` is a fine template.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents (researcher, planner) MUST read these before acting.**

### Requirements & roadmap
- `.planning/REQUIREMENTS.md` §CourtListener trace (TRACE-xx) — TRACE-05, TRACE-06, TRACE-07 are the must-haves
- `.planning/ROADMAP.md` §Phase 3 — 3 success criteria
- `.planning/PROJECT.md` — "Second trace source" section; cross-workload comparison motivation

### Technical research (Phase 1 + milestone outputs)
- `.planning/research/STACK.md` §Court records API: CourtListener v4 — endpoint taxonomy, sizes, auth, pacing (0.8 s + 0–0.4 s jitter, ~3,200 req/hour ceiling well under 5,000/hr)
- `.planning/research/PITFALLS.md` §C1 PACER/RECAP/CL confusion — hard allowlist for `www.courtlistener.com`
- `.planning/research/PITFALLS.md` §C2 Rate limits — 5,000/hr authenticated, v2 compat gets 1,000/endpoint/hour
- `.planning/research/PITFALLS.md` §C3 Gated endpoints — `/docket-entries/`, `/recap-documents/`, `/recap-query/` excluded from mix
- `.planning/research/PITFALLS.md` §C4 Search 10-min cache — detail endpoints are fine; don't use search
- `.planning/research/PITFALLS.md` §M5 Opinion-text variance (5 KB – 500 KB) — drives the 80/20 `?fields=` decision (D-01)
- `.planning/research/PITFALLS.md` §m1 plain_text dominates response size — same as M5 root cause

### Phase 1 artifacts (prerequisites)
- `scripts/pilot_court_trace.py` — ENDPOINTS dict, pilot-tuned ID ranges, per-endpoint tally format (templates D-06 fallback + D-10 tally)
- `scripts/collect_trace.py` — Congress production collector; THE copy-modify template (per-row append, backoff, jitter loop)
- `traces/court_pilot.csv` — 178 successful rows, validates schema and sizes
- `results/court/pilot_report.txt` — pilot success baseline (docket 90% / opinion 74% / cluster 90% / court 100%)
- `.planning/phases/01-enabling-refactors-courtlistener-pilot/01-CONTEXT.md` — D-04/D-05/D-07/D-08/D-09 locked precedents for results/court/ layout and failure policy

### Phase 2 artifacts (enabling code)
- `include/wtinylfu.h` + `scripts/plot_results.py` (W-TinyLFU styling via `#8c564b` / `P` marker) — Phase 2 ensures the sixth policy is ready for the 6-policy sweep in D-12
- `src/main.cpp` `make_policy(name, capacity_bytes, n_objects_hint)` — Phase 2 widened signature; calling `--policies wtinylfu` over court trace works identically to Congress

### Simulator surfaces to read before planning
- `src/main.cpp` alpha-sweep loop and `--alpha-sweep` / `--replay-zipf` / `--output-dir` flags — Phase 3 does NOT modify main.cpp
- `src/workload_stats.cpp` — produces `workload_stats.json` already for Congress; Phase 3 invokes the same path on the court trace (D-13)
- `Makefile` `run-sweep` target + `plots` target — may need a `--workload court` passthrough if not already flag-pluggable

### External API
- CourtListener REST v4 docs: https://www.courtlistener.com/help/api/rest/v4/ (Context7: `/websites/courtlistener_help_api_rest`)
- CourtListener PACER docs: https://www.courtlistener.com/help/api/rest/pacer/ (Context7: `/websites/courtlistener_help_api_rest_pacer`) — informational; no endpoints from here are used

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `scripts/collect_trace.py` — Congress production collector: rate-limit loop with jitter + backoff, per-row CSV append, endpoint generators. Copy-modify pattern — do NOT generalize into a shared module (Phase 1 CONTEXT.md line 93, STACK.md §32).
- `scripts/pilot_court_trace.py` — ENDPOINTS dict with `id_range` tuples, per-endpoint tally printer, `.env`-based token read. Direct template for D-05 endpoint draws and D-10 tally.
- `traces/court_pilot.csv` — 178 confirmed-format rows (`timestamp,key,size`) validating the schema the production collector must emit.
- `src/workload_stats.cpp` (Phase 1) — produces `results/{workload}/workload_stats.json` with mean size, unique-object count, α MLE, OHW ratio. Reused verbatim for D-13.
- `src/main.cpp` `--alpha-sweep` / `--replay-zipf` / `--output-dir` path — produces `mrc.csv` + `alpha_sensitivity.csv`. Called on court trace via D-12 without any source changes.
- `scripts/plot_results.py` — Phase 2 added W-TinyLFU entry (brown `#8c564b`, `P` marker). `make plots --workload court` regenerates `results/court/figures/*.pdf` automatically.

### Established Patterns
- **CSV format `timestamp,key,size`** — fixed across both workloads. Timestamp in milliseconds since epoch, key = URL path suffix, size = response bytes.
- **`.env` token pattern** — `COURTLISTENER_API_KEY` committed to `.env.example`; actual token in gitignored `.env`; collector reads via `os.environ.get` and exits non-zero if missing.
- **Hard host allowlist** — pilot uses `ALLOWED_HOST = "www.courtlistener.com"` + BASE_URL check. Production collector must carry this forward (per PITFALLS C1).
- **Per-workload result subdirs** — `results/congress/` and `results/court/` are symmetric; analysis scripts iterate via `--workload` flag.
- **CSV-as-contract** — any schema change (e.g., adding a column) would break Phase 1's plot pipeline AND Phase 5's compare_workloads.py. Keep columns identical.
- **Resume-by-flag, not auto-resume** — `--resume` is opt-in to prevent silent extension of a stale CSV (D-09).

### Integration Points
- `traces/court_trace.csv` → input to `./cache_sim --trace ...` → `results/court/mrc.csv` + `alpha_sensitivity.csv`
- `results/court/workload_stats.json` ← `./cache_sim --workload-stats-only` (D-13) → read by Phase 5's `compare_workloads.py`
- `results/court/collection_report.txt` ← Python collector (D-10 tally) → human-readable, grep-friendly
- `Makefile` `run-sweep --workload court` — may need a minor target update to parameterize `--output-dir`; if already driven by an env var or similar, zero change required
- No C++ source changes. No changes to `include/cache.h`, `include/wtinylfu.h`, or any policy header. Phase 3 is a pure data + sweep phase.

</code_context>

<specifics>
## Specific Ideas

- **Pilot script stays.** `scripts/pilot_court_trace.py` is not deleted or merged — it's a standing verification tool. The production `scripts/collect_court_trace.py` is a sibling, not a replacement. (Phase 1 CONTEXT §specifics.)
- **Use the pilot's ID ranges verbatim for first run.** The pilot's `id_range` tuples were tuned to clear the 70% gate on 4/4 endpoints. D-06 fallback only fires if production re-observes <60% on the first 500 of an endpoint.
- **6.1-hour runtime means overnight.** The collector should be designed to run headless with logging going to both stdout and `results/court/collection_report.txt.progress` (progress file truncated on crash-free exit). No interactive prompts, no TTY assumptions.
- **Hard-coded 4 endpoints.** No flag for adding/removing endpoints in production. The pilot validated the mix; any change expands scope into "rethink endpoint selection" which is NOT in this phase.
- **The sweep is a single `make run-sweep --workload court` invocation.** If the Makefile target doesn't already support this, the plan should make the minimal change (parameterize `--output-dir` and/or `--trace`) — NOT rewrite the target.
- **`plain_text`-null opinions are part of the story.** Some opinions have only HTML or PDF bodies. The 20% full-fetch draws occasionally land on null-plain_text opinions — that's fine per D-04. Do NOT retry or filter; the size-distribution is what it is.

</specifics>

<deferred>
## Deferred Ideas

- **Cross-workload comparison table (Congress vs Court side-by-side)** — Phase 5 (ANAL-03). Phase 3 emits the data; Phase 5 joins and plots.
- **Multi-seed confidence intervals on court** — Phase 5 (ANAL-02). Single-seed court sweep is sufficient for Phase 3's TRACE-07 gate.
- **Doorkeeper ablation on court trace** — Phase 4 (DOOR-03).
- **SHARDS large-scale on court trace** — NOT in Phase 4 either. Phase 4 uses a 1M synthetic trace (SHARDS-01..03) because real traces at 20K don't stress SHARDS' sampling rates.
- **Separate `v4` vs `v3` endpoint fallback** — CourtListener v4 is the locked target. No v3 fallback.
- **Dynamic endpoint mix (e.g., pause an endpoint if its success drops mid-run)** — overkill. D-06 fallback at first-500 is enough.
- **Trace provenance manifest** (git SHA, collector version, timestamp metadata JSON) — deferred; Phase 5 or the final report can reconstruct this from the commit log.

</deferred>

<scope_creep_log>
## Scope Creep Redirected

None during this discussion — the user stayed within the trace-collection + replay-sweep scope. All 15 decisions bound the implementation without expanding capability.

</scope_creep_log>

---

*Phase: 03-courtlistener-trace-collection-replay-sweep*
*Context gathered: 2026-04-19*
