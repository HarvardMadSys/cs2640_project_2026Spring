# Domain Pitfalls

**Domain:** Cache eviction-policy simulator extension (court-records trace, W-TinyLFU, larger-scale SHARDS, submission writeup)
**Researched:** 2026-04-16
**Overall confidence:** HIGH for API limits (CourtListener docs via Context7) and W-TinyLFU design (Caffeine wiki/source via Context7); MEDIUM for writeup pitfalls (community wisdom synthesized from systems-paper experience).

---

## Critical Pitfalls

These cause outright failure — lost days on trace collection, wrong W-TinyLFU numbers, un-defensible comparisons, or a broken live demo in front of the class.

### Pitfall C1: Confusing PACER (paid) with RECAP/CourtListener (free) and getting stuck on billing

**What goes wrong:** Treating "PACER" as the data source and discovering mid-collection that every docket page costs $0.10/page (capped at $3 per doc) and each search is $0.10 per page-of-results. A 20K-request trace against raw PACER could cost $500-$2000, and PACER credentials require a billing account with SSN/EIN verification.
**Why it happens:** PACER, RECAP, and CourtListener are frequently conflated. PACER is the fee-paid federal courts portal; RECAP is a browser extension that uploads documents users have already paid for into a free archive; CourtListener is the Free Law Project website that hosts that archive and exposes it via REST API.
**Consequences:** Either the trace collector bills the student's credit card, or it's built against an endpoint that the student's PACER account doesn't have authorization for (many users). Weeks of debugging "403 Forbidden" before realizing the account isn't billable.
**Prevention:**
- Target **CourtListener REST v4** (https://www.courtlistener.com/api/rest/v4/) for all collection. It is free for authenticated users up to 5,000 queries/hour (per CourtListener API docs, Context7 `/websites/courtlistener_help_api_rest`).
- Never hit `*.uscourts.gov` directly from the collector. If the data isn't in RECAP already, pick different data.
- Put a hard-coded host allowlist in the collector: only `www.courtlistener.com` is permitted.
**Detection:** Warning sign is any URL containing `pacer.uscourts.gov`, `ecf.`, or a login form. If the collector prompts for a password instead of using a token, it's the wrong API.
**Phase:** Trace collection phase (TRACE-03 / TRACE-04). Must be the first decision before writing any HTTP code.

### Pitfall C2: Hitting the 5,000-req/hr global throttle or 1,000/endpoint/hr v2 throttle mid-collection

**What goes wrong:** CourtListener v4 authenticated users get 5,000 queries/hour globally; older v2 clients are throttled to **1,000 queries per endpoint per user per hour** (per Context7 `/websites/courtlistener_help_api_rest`, "Limitations"). A naive parallel collector bursts to 50 rps and gets 429-throttled for the rest of the hour — a 20K trace that should take 5 hours stretches to 20+ hours with wasted retries.
**Why it happens:** The same bug as the Congress.gov v1 collector (fixed-interval, no jitter, no backoff). Each rewrite of the rate-limit code tends to forget that 429s from CourtListener can persist for minutes, not seconds.
**Consequences:** Collection stalls, backoff caps pile up, session times out, partial trace is truncated.
**Prevention:**
- Target **~1 request/second** with **1.2–2.0s jittered sleep** (same cadence that worked for Congress.gov v3 per `PROCESS.md` Phase 3).
- Use `Authorization: Token <token>` header, **never** HTTP Basic auth (v2 only; also doubles your exposure if leaked).
- On 429, read the `Retry-After` header if present; fall back to exponential backoff with a 300s cap.
- Log throttle events and abort if 5 consecutive requests throttle — something is wrong, keep going only wastes quota.
- **Debug tip from CourtListener docs**: if API fails but the browsable interface works, your token auth is broken and you're being throttled as an anonymous user. Verify auth by curl-ing a single endpoint before starting a 20K run.
**Detection:** Monitor 429 rate and running tally. If 429s exceed 1% of responses, stop and investigate.
**Phase:** Trace collection phase (TRACE-04). Bake into the initial collector design; retrofitting throttling code is painful.

### Pitfall C3: Building the trace against an endpoint that's "select users only" without realizing it

**What goes wrong:** Per CourtListener PACER docs (Context7 `/websites/courtlistener_help_api_rest_pacer`), three of the most useful endpoints — `docket-entries`, `recap-documents`, and `recap-query` — are labeled **"available to select users only. Please contact support for access."** A collector written against these returns 403 for an unauthorized account, not a clear error message.
**Why it happens:** The endpoint index page shows the URL and parameters without always flagging the permission gate. You discover it at runtime.
**Consequences:** Have to rewrite the collector against a different endpoint, re-characterize the workload, and possibly lose the "legal documents are big" contrast story.
**Prevention:**
- Pick endpoints that are **publicly accessible without special access**:
  - `/api/rest/v4/dockets/` — docket metadata (case captions, party names, dates). Returns JSON metadata; sizes are manageable (a few KB each).
  - `/api/rest/v4/opinions/` — opinion text (case law). Listed alongside clusters; full opinion text can be large (tens to hundreds of KB), which gives the size-distribution contrast vs. Congress bill JSON.
  - `/api/rest/v4/clusters/` — opinion clusters (groupings with metadata and sub-opinions).
  - `/api/rest/v4/courts/` — small metadata, good for weighted selection (analogous to 119th Congress weighting).
- **Test a single unauthenticated-user request to each endpoint from a throwaway account before writing the collector.** If any returns 403, drop it from the plan.
- If the story needs docket entries or RECAP documents, email CourtListener support **at project start** requesting access; wait times are 1–7 days in the author's experience.
**Detection:** 403 Forbidden responses with the message `"You do not have permission to perform this action."`
**Phase:** Trace collection phase (TRACE-03 research step).

### Pitfall C4: Query-result caching silently invalidates "independent accesses"

**What goes wrong:** Per CourtListener search docs (Context7): **"All query results are cached for ten minutes."** The *Search* API specifically returns cached results for identical queries within a 10-minute window. Even on the detail endpoints, a CDN may cache. A collector that re-requests the same URL every few seconds is testing CourtListener's cache, not generating independent accesses for ours.
**Why it happens:** Invisible: the HTTP response looks identical. Rate is counted against the quota but the underlying database isn't hit.
**Consequences:** This is largely OK for `replay-Zipf` (we only use the unique (key, size) pairs, and each endpoint is typically hit once during collection). But if the collector is designed with any repeat-visits intent (e.g., to verify consistency), the data is server-side cached and your trace doesn't reflect what you think.
**Prevention:**
- Unique-key collection only. Each endpoint URL should be distinct in the raw trace.
- Use the `/api/rest/v4/` *detail* endpoints for size ground truth (resource-body bytes), not the `/search/` endpoint.
- If you do need to sample popularity empirically (vs. overlay Zipf), do not use CourtListener; the cache layer makes popularity unobservable.
- Don't rely on response size as a reliability check — a CDN-cached response will have the same size on two fetches even if the backing row changed.
**Detection:** If you see a suspiciously uniform response time (everything under 50ms), you are hitting a cache; real queries are 100–500ms.
**Phase:** Trace collection phase — especially if anyone proposes "sample a distribution from real hits."

### Pitfall C5: Count-Min Sketch without decay/aging produces stale frequency forever

**What goes wrong:** The TinyLFU frequency sketch needs a **reset (freshness) mechanism** or the cache becomes immutable after warmup: once an object has counted-min frequency 15 (the 4-bit cap), nothing new can be admitted because every candidate has lower estimated frequency than any long-tenured resident.
**Why it happens:** The original Count-Min Sketch paper doesn't describe decay — Einziger & Friedman 2017 (ACM TOS, "TinyLFU: A Highly Efficient Cache Admission Policy") added the "conservative update + aging" step specifically for caching. Implementers who start from a generic CMS tutorial produce a non-aging sketch.
**Consequences:**
- W-TinyLFU numbers look great during warmup and then plateau; the "main" region effectively becomes static.
- Against replay-Zipf where popularity is stationary this is *less* visible, but the tail behavior is still wrong.
- When compared against Caffeine's published W-TinyLFU numbers, ours will be systematically worse at small caches.
**Prevention:**
- Implement the **periodic halving reset** from the Caffeine `FrequencySketch` source (Context7 confirms the class name `com.github.benmanes.caffeine.cache.FrequencySketch`). Every N accesses (typically N = 10 * cache_capacity), right-shift every 4-bit counter by 1 bit (integer divide by 2). This preserves relative ordering while ensuring bounded growth and freshness.
- Use **conservative update**: when incrementing, only increment the minimum counter across hashes, not all of them. This halves the over-count error.
- Cap counters at 15 (4 bits) — increments above 15 are no-ops. Caffeine uses 4-bit counters and 8 bytes/element; matching this enables apples-to-apples comparison against Caffeine numbers.
- **Validate against the original paper's numbers**: Einziger & Friedman show W-TinyLFU hit rates on SPC-1, WS, and OLTP traces. Even if we don't run those exact traces, a ~40% hit rate at 10% cache on a Zipf(0.8) trace is in the published ballpark.
**Detection:**
- Warning sign 1: hit rate climbs during first ~N accesses and then **decreases** (new items can't break in).
- Warning sign 2: admission-acceptance rate falls below 1% after warmup (every candidate rejected).
- Warning sign 3: W-TinyLFU performs *worse than LRU* at any cache size — this is a bug; published results show W-TinyLFU ≥ LRU across all Zipf regimes.
**Phase:** Implementation phase (SIM-09 / SIM-10). Must be built in from the start; retrofitting decay into a mature sketch requires re-running every experiment.

### Pitfall C6: Window sizing, admission logic, and victim comparison edge cases

**What goes wrong:** The W-TinyLFU paper specifies a **1% LRU window + 99% SLRU main with 20%/80% protected/probation split** as the default static configuration. Getting any of these wrong produces subtly incorrect results that still "look plausible":
- Window too big (e.g., 10%) → behaves more like LRU, understates TinyLFU's contribution.
- Window too small (e.g., 0.01%) → new items can never accumulate frequency, everything cold-starts and is rejected.
- Victim comparison done against *main probation* victim, not *window* victim, or vice versa.
- "Frequency tie" rule: when candidate freq == victim freq, Caffeine admits the candidate with a small random probability (hash jitter) to avoid HashDoS amplification attacks — per the Caffeine design wiki (Context7 confirms: "introduce a small jitter during comparisons ... probability below 1%"). Deterministic tie-breaking biases toward incumbents and understates W-TinyLFU.
- **Doorkeeper Bloom filter**: optional pre-filter in front of the main CMS. If skipped (valid simplification) you lose some single-hit-wonder protection; if implemented incorrectly (too small, not reset with CMS aging) it causes false-positive admits.
**Why it happens:** Six parameters, three paths (window→main, main→eviction, ghost→main), and fuzzy tie-breaking.
**Consequences:** Numbers that are 2-5% off published W-TinyLFU and are attributed to "different workload" when they're actually implementation bugs.
**Prevention:**
- **Use the static config** (1% window, 99% SLRU main, 20% protected / 80% probation) — skip the hill-climbing adaptive sizing. Per Caffeine wiki, "Hill climbing algorithm" is an optimization on top of static W-TinyLFU and adds significant complexity for marginal gain.
- **Decide upfront whether Doorkeeper is in or out.** Recommended: **omit Doorkeeper** for this project. The simpler 4-bit CMS + SLRU is enough to demonstrate the idea, halves the implementation surface, and Caffeine itself ships without a classical Doorkeeper (it uses a single 4-bit sketch).
- **Test admission logic against a handcrafted trace**: 100 accesses where one object is touched 20 times and 99 are touched once. After warmup, the hot object should live in the protected zone and survive a scan of 1000 unique misses. Write this as a unit test before running on real traces.
- **Reference the Caffeine simulator Java source**, specifically `com.github.benmanes.caffeine.cache.simulator.policy.sketch.WindowTinyLfuPolicy` (not the hill-climber variant). Translate the decision logic line-by-line; do not paraphrase the paper.
- **Cite Einziger & Friedman 2017 (ACM TOS 13.4, Article 16) and the Caffeine wiki** in the final report and any code comments — makes the implementation auditable.
**Detection:**
- Unit test the "20-access hot object survives 1000-access scan" invariant.
- Cross-check: W-TinyLFU should beat LRU at every cache size on Zipf(0.8). If it doesn't, implementation is wrong.
- Cross-check: at alpha=0.0 (uniform), W-TinyLFU should be within 1-2% of LRU (pure recency helps nothing when nothing is hot). If it's significantly worse, the window is too small.
**Phase:** Implementation phase (SIM-10 / SIM-11). Budget at least 1.5x the time you'd expect for LRU.

### Pitfall C7: Live demo fails because of slow trace I/O, heavy plotting, or missing files

**What goes wrong:** Running the simulator live in front of the class. The typical failure modes:
- The trace file is 200 MB; `load_trace` takes 30 seconds while everyone waits.
- `matplotlib` blocks the terminal waiting for a window that never shows (SSH without X, or macOS display sleep).
- DYLD_LIBRARY_PATH for the broken Homebrew libexpat is missing from the demo shell → immediate `ImportError`.
- `.env` is not loaded in the demo session → trace collector prompts for a key and dies.
- The large 1M-access SHARDS trace is not committed and regenerating it on the demo laptop takes 90 seconds.
- `alpha_sweep` is run as a default demo step and takes 3 minutes per alpha on the demo trace (per CONCERNS.md: "Alpha sweep regenerates trace per alpha value").
- The presenter's laptop goes to sleep mid-demo, disconnects from the projector, and the terminal session is lost.
**Why it happens:** Demo environments are not the dev environments where the simulator was built. macOS lid-close defaults to sleep; fonts cache differently; DYLD is stripped from GUI-launched shells.
**Consequences:** The demo crashes or hangs visibly in the middle. Class remembers the crash, not the research.
**Prevention:**
- **Pre-render the heavy plots.** The demo shows pre-computed PDFs/PNGs side-by-side with a *fast* live run. Never generate the MRC figures live.
- **Use a tiny demo trace** (~10K accesses, pre-loaded into a small CSV). Live runs should finish in <10 seconds to be engaging.
- **Write a `demo.sh` script** that: (1) sources `.env`, (2) sets `DYLD_LIBRARY_PATH`, (3) runs the exact three or four commands in the exact order, (4) has `echo` separators so the terminal narrates itself. Practice with this script, not ad-hoc commands.
- **Record a screen capture** of the full demo working end-to-end the night before. If the live demo fails, cut to the recording. This is not cheating — it's risk mitigation.
- **Disable screen sleep, notifications, and dock animations** before starting.
- **Close every browser tab and app not needed for the demo** — reduces memory pressure and prevents embarrassing notification popups.
- **Have a static backup slide** with the final numbers table, ready if the simulator hangs.
**Detection:** If the demo script takes >30 seconds on your own laptop, it will hang on the demo machine too. Prune.
**Phase:** Demo prep (DOC-04). Practice 3+ times on the target machine.

---

## Moderate Pitfalls

These don't kill the project but produce a weaker writeup or reviewer pushback.

### Pitfall M1: Cross-workload comparison normalization traps

**What goes wrong:** Declaring "SIEVE wins on Congress but S3-FIFO wins on court records!" when the two traces differ in OHW ratio, alpha, object-size distribution, and unique-object count simultaneously. The comparison conflates policy sensitivity with workload characteristics.
**Why it happens:** Replay-Zipf lets us fix alpha, but we cannot separately fix OHW ratio (that's determined by unique object count relative to trace length) or size distribution (inherent to each trace's domain). Two traces are never "just like each other except for domain."
**Consequences:** A reviewer (or the professor) asks "Is this a domain finding or an alpha=0.8 finding?" and the writeup has no answer.
**Prevention:**
- **Run both traces at the same alpha values** — ideally the alpha sweep {0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2} on both, not just one.
- **Report in normalized cache units** (% of working set, not absolute bytes or absolute object counts). The existing project already uses % of working set — keep doing this.
- **Show the workload-characterization panel side-by-side for Congress vs. court records**: alpha, OHW ratio, unique objects, median size, size IQR. Then the reader can attribute differences themselves.
- **If the two traces produce similar policy rankings, write it up as a finding, not a disappointment.** "Policies' relative ordering is robust across legal public-records workloads" is a defensible claim. The proposal (PROJECT.md "Risks") already anticipates this.
- **Avoid the temptation to cherry-pick the cache size that makes the comparison most dramatic.** Show the full MRC or a table of 4+ cache sizes.
**Detection:** If the conclusion only holds at one specific cache size or one specific alpha, it's cherry-picked.
**Phase:** Analysis & writeup (ANAL-01, ANAL-02).

### Pitfall M2: Presenting raw numbers without uncertainty / variance

**What goes wrong:** The results table (e.g., PROCESS.md "Phase 5: Results Summary") shows `SIEVE = 0.462 at 10% cache` as a single number. It was generated from one seed, one replay-Zipf sample. A reviewer reasonably asks: "What's the variance? Is 0.462 vs. LRU's 0.555 significant or within noise?"
**Why it happens:** One-seed results are what the code emits by default. Running a sweep over 5 seeds doubles the runtime.
**Consequences:** The reported differences between S3-FIFO (0.499) and SIEVE (0.462) — 3.7 percentage points — may be within seed-to-seed noise. If so, the "SIEVE is strictly best" claim is overreach.
**Prevention:**
- **Run each replay-Zipf experiment with 5+ seeds.** Report mean ± std. dev. per (policy, cache size, alpha).
- For alpha sweeps and cache sweeps, **error bars on the line plots** (shaded band, not error-bar ticks, for readability).
- For the main results table, flag any difference smaller than 2 std. dev. as not significant — don't claim a ranking you can't defend.
- **Fix the seed in the code** so reruns are reproducible, and **document which seeds were used** in the README.
- For W-TinyLFU specifically: both the random hash jitter in admission and the CMS hash seeds are sources of variance; this is *not* optional for W-TinyLFU.
**Detection:** If you can't answer "how many seeds did you run?" you didn't run enough.
**Phase:** Analysis phase (after implementations are complete). One extra night of wall-clock.

### Pitfall M3: Showing MRC as a few discrete points instead of a full curve

**What goes wrong:** Reporting four or five cache sizes (0.1%, 1%, 5%, 10%) as a table and a line plot. The reader cannot tell what happens at 2% or 20%, and misses obvious phase-transition behavior (e.g., W-TinyLFU beats SIEVE sharply at very small caches and converges at large caches — this pattern needs the full MRC).
**Why it happens:** Five data points is what the demo runs.
**Consequences:** Paper reviewers (and the professor) discount results that don't show full MRC curves.
**Prevention:**
- **Sweep cache size on a log scale with ≥20 points** (e.g., 0.05%, 0.1%, 0.2%, 0.5%, 1%, 2%, 5%, 10%, 20%, 50% of working set). The existing pipeline can do this; it's just a matter of the sweep range.
- **Show both the MRC and the byte-MRC** — the existing plotting pipeline already produces these.
- **Overlay SHARDS approximation on exact MRC** for at least one trace to demonstrate SHARDS correctness visually.
- Include a **zoomed inset** at very small caches (<1%) where policy differences are largest — this is conventional in cache papers.
**Detection:** If a reader could fit a straight line through your 4 points, you have not shown an MRC.
**Phase:** Analysis / plotting.

### Pitfall M4: SHARDS validation at 1M-scale without reporting the sampling-rate-vs-accuracy table

**What goes wrong:** Generating a 1M+ trace for SHARDS validation (SIM-12) but only showing one accuracy number (e.g., "MAE = 0.003 at 1% sampling"). The Waldspurger et al. FAST'15 paper specifically reports a table of MAE across sampling rates ({0.0001, 0.001, 0.01, 0.1}) — without that table, the contribution is unclear.
**Why it happens:** The existing validation framework only collects MAE at three rates on small traces.
**Consequences:** Can't make the claim "SHARDS hits its paper target (0.1% MAE at 1% sampling) at million-scale" — which is the whole point of SIM-13.
**Prevention:**
- **Run the four sampling rates {0.01%, 0.1%, 1%, 10%} on the 1M-trace** and produce a table of (rate, sampled-count, MAE, max-error).
- **Also plot the SHARDS vs. exact MRC at the lowest rate where it's still accurate** — this is the "money shot" of the SHARDS contribution.
- **Exact stack distance at 1M scale requires an O(n log n) oracle** (Mattson et al.). The current exact implementation is O(n²) and capped at 50K (`src/main.cpp:289`). Either replace it with a balanced-BST implementation (CONCERNS.md "Scaling Limits") or use a 50K sub-trace as the exact reference and report SHARDS accuracy in two regimes: "at 50K with exact oracle" and "at 1M with self-consistency."
- **Address the unused-low-rate trap from CONCERNS.md**: on a 40K trace, 0.1% sampling produces 17 samples. On a 1M trace it produces 1,000. Report only sampling rates that produce ≥200 samples — this is the fragile-areas recommendation already flagged in CONCERNS.md.
**Detection:** If the SHARDS section of the writeup is shorter than one page, it probably has only one number.
**Phase:** SHARDS rigor phase (SIM-12, SIM-13) and writeup.

### Pitfall M5: Ignoring opinion-text response-size variance (5 KB – 500 KB) in byte-MRC analysis

**What goes wrong:** Court opinion endpoints return widely variable response sizes: some opinions are 2 KB stubs, others are 500 KB supreme-court decisions with dissents and concurrences. If the collector naively records the full JSON (including all sub-opinions) for every cluster, object sizes span three orders of magnitude. This **changes byte-MRC behavior substantially vs. object-MRC**, in ways that neither dataset alone exposes.
**Why it happens:** Congress.gov bill JSON is fairly uniform (median 231 bytes per PROCESS.md). Opinion text is not.
**Consequences:**
- Byte-MRC and object-MRC plots show *more* divergence for court records than for Congress, which could be reported as a finding or as confusion.
- A single 500 KB opinion can dominate the working-set bytes and distort cache-size percentage interpretation.
**Prevention:**
- **Characterize the size distribution for court records BEFORE running the policy sweep**. Compute median, 95th percentile, max, IQR. Add this to the workload characterization panel.
- **Consider using `/api/rest/v4/clusters/` without including sub-opinion bodies** (use the `fields=` parameter or `?omit=sub_opinions` if supported) — keeps sizes more manageable. Trade-off: loses the big-object realism.
- If byte-MRC and object-MRC diverge dramatically on court records, **write that up as a finding**: "policies are more sensitive to object-size variance on document workloads than on metadata workloads."
- **Do not silently pool small and large objects** in the alpha estimator; MLE on size-weighted frequencies is different from MLE on access-count frequencies.
**Detection:** If the 95th-percentile object size is >100x the median, the two MRC flavors will diverge.
**Phase:** Trace collection (TRACE-05) and workload characterization.

### Pitfall M6: Running the alpha sweep per-alpha in replay-Zipf mode without caching the shuffled object list

**What goes wrong:** Already called out in CONCERNS.md: "Alpha sweep regenerates trace per alpha value" (`src/main.cpp:219-235`). With a second, larger trace (court records ≥20K) and a sixth policy (W-TinyLFU), the 7-alpha sweep × 6 policies × 2 traces is 84 simulator runs; the unnecessary trace regeneration compounds.
**Why it happens:** `replay_zipf(raw_trace, n, alpha)` does the full shuffle+CDF inside the loop.
**Consequences:** A 10-minute sweep becomes an hour; the final experiment-running night is stressful.
**Prevention:**
- **Before the big sweep, refactor** `replay_zipf` to accept a pre-built shuffled object list (CONCERNS.md suggests `replay_zipf_prepared(objects, num_requests, alpha)`).
- Alternatively, **cache the ZipfGenerator CDF keyed on `(n, alpha, seed)`**.
- This is a listed tech-debt item; cash it in before the final runs.
**Detection:** Time one alpha-sweep run. If it takes >5 minutes on the laptop where the demo will run, fix the regeneration.
**Phase:** Pre-experiment refactor, before the cross-workload comparison (ANAL-01).

---

## Minor Pitfalls

Won't derail anything but are small footguns.

### Pitfall m1: Opinion-text JSON includes `plain_text` which dominates response size

**What goes wrong:** Per CourtListener PACER docs (Context7 `/websites/courtlistener_help_api_rest_pacer`): *"plain_text — Contains the extracted text of the document. OCR may be used. Omit this field via Field Selection to reduce response size."* If the trace collector does `GET /opinions/<id>/` without field selection, every response carries the full opinion text. That's the size we want for realism, but it's also what triggers rate limits faster (larger bytes-per-request × throttling on payload size in some CDN policies) and may exceed 10 MB per response on the very longest opinions.
**Prevention:** Decide once whether the trace sizes reflect *full document* or *metadata only*, and be explicit in the report. Either is defensible; conflating them is not.
**Phase:** Trace collection.

### Pitfall m2: v4 cursor pagination "Invalid cursor" error on parameter changes

**What goes wrong:** Per CourtListener migration guide (Context7): changing any GET parameter while a cursor is active yields `404 Invalid cursor`. A naive collector that paginates through a large endpoint and adjusts `page_size` mid-stream will crash.
**Prevention:** Fix all GET params before the first request; never mutate between cursor follow-ups. Log the first cursor URL and reuse; don't reconstruct.
**Phase:** Trace collection.

### Pitfall m3: Court IDs don't match PACER subdomains one-to-one

**What goes wrong:** Per CourtListener PACER docs: `azb` → `arb`, `cofc` → `uscfc`, and other renames. A collector that hardcodes "the PACER subdomain list" for weighted selection will miss or duplicate courts.
**Prevention:** Pull the court list from `/api/rest/v4/courts/` at collection start; don't hardcode.
**Phase:** Trace collection.

### Pitfall m4: Not seeding CMS hash functions or sketch aging clock

**What goes wrong:** Two W-TinyLFU runs with identical parameters produce different miss ratios because the internal hash seeds differ. Goes under "variance" in M2 but is specifically about hashing, not just trace sampling.
**Prevention:** Take a `uint64_t seed` parameter at W-TinyLFU construction; derive all four CMS hash seeds from it deterministically. Commit the default seed.
**Phase:** Implementation (SIM-09).

### Pitfall m5: Comparing against published W-TinyLFU hit rates without matching the trace

**What goes wrong:** Caffeine's simulator reports 45.25% hit rate on a particular trace; we report 53.8% on ours. Saying "ours is higher than Caffeine's — great!" is meaningless because the traces are different (Caffeine's numbers are from a specific trace format, e.g., `linked.Lru` showing 20.24% hit rate on the same trace, per Context7). The only valid cross-check is **relative** (W-TinyLFU > LRU by ≥15 points in high-skew regimes) or **on a shared trace** (run our W-TinyLFU on a Caffeine-supplied trace like Gradle or Lirs).
**Prevention:**
- For the writeup, compare our W-TinyLFU to **our LRU/SIEVE/S3-FIFO** (apples to apples) and cite Caffeine results only qualitatively.
- Optional stretch: run our W-TinyLFU on **one Caffeine trace** (the Caffeine simulator accepts multiple formats per Context7; Caffeine's Gradle trace is a standard benchmark). If our numbers fall within ±2% of Caffeine's, that's a strong validation.
**Phase:** Writeup / analysis.

### Pitfall m6: Not documenting Claude Code mistakes in the AI-use report

**What goes wrong:** DOC-03 (AI-use report) was specifically requested by the professor. The natural tendency is to write about successes ("Claude scaffolded the simulator!") and omit failures. PROCESS.md already has a gold mine of "Claude got this wrong" incidents (S3-FIFO algorithm, SHARDS denominator, scientific direction). Omitting them is less honest and less useful to the professor.
**Prevention:**
- **List the prior-fixed bugs as AI-use learning moments.** Each of these was caught by human review after Claude produced code that looked plausible: MRC units, per-access sizes, S3-FIFO algorithm, ZipfGenerator OOB, size-on-hit, SHARDS denominator.
- Frame each as: "Claude wrote X. Looked correct. Code review caught it because of Y. Here's what I'd do differently: write handcrafted test cases before accepting generated policy code."
- This is *more* credible than a pure "Claude was great" report — it shows engineering judgment.
**Phase:** Writeup (DOC-03).

---

## Phase-Specific Warnings

| Phase Topic                          | Likely Pitfall                                                           | Mitigation                                                                                     |
| ------------------------------------ | ------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------- |
| TRACE-03 (pick source)               | C1 PACER billing, C3 "select users only" endpoints                       | Host-allowlist to CourtListener; unauthenticated-test each planned endpoint                    |
| TRACE-04 (collector)                 | C2 5k/hr throttle, C4 10-min caching, m1 response-size blowup            | 1.2–2.0s jitter, token auth verified by single-request test, field-selection to omit full text |
| TRACE-05 (collect ≥20K)              | M5 size variance, m3 court-ID mapping                                    | Characterize size dist early; pull court list from API                                         |
| TRACE-06 (replay-Zipf on court)      | M1 cross-workload normalization                                          | Run same alpha sweep on both traces; plot workload characterization panels side-by-side        |
| SIM-09 (Count-Min Sketch)            | C5 no decay, m4 unseeded hashes                                          | Implement periodic halving + conservative update; take a seed param                            |
| SIM-10 (W-TinyLFU)                   | C6 window/SLRU/admission edge cases                                      | Mirror Caffeine simulator source; unit test "hot object survives scan"                         |
| SIM-11 (add to main comparison)      | m4 variance, M6 alpha-sweep trace regeneration                           | Multi-seed runs; refactor `replay_zipf` to accept prepared object list                         |
| SIM-12/13 (SHARDS rigor)             | M4 single-number report, O(n²) exact oracle limit                        | Report 4-rate MAE table; use 50K subtrace for exact baseline + 1M for self-consistency         |
| ANAL-01 (cross-workload)             | M1 confounded variables, M3 discrete-point MRC                           | Fixed alpha sweep both traces; full-MRC log-scale sweep                                        |
| ANAL-02 (when each policy wins)      | M2 no uncertainty, M3 single-cache-size cherry-pick, m5 Caffeine compare | Seeds + std-dev bands; full MRC; qualitative-only Caffeine references                          |
| DOC-02 (final report)                | M2 no variance, M3 skimpy plots                                          | Report mean ± std; log-scale MRC; full table                                                   |
| DOC-03 (AI-use report)               | m6 successes-only                                                        | Include the 9-bug list from PROCESS.md as learning moments                                     |
| DOC-04 (live demo)                   | C7 environment drift, slow I/O, DYLD                                     | `demo.sh` script; pre-rendered plots; screen recording backup; disable sleep                   |

---

## Sources

- **CourtListener REST API (rate limits, etiquette, endpoint permissions):** Context7 library `/websites/courtlistener_help_api_rest` (https://www.courtlistener.com/help/api/rest) — HIGH confidence, directly from CourtListener's published API docs. Key facts used: 5,000 queries/hour limit for authenticated v4 users; v2 1,000/endpoint/hr limit; 429 Too Many Requests on exceeding; token auth via `Authorization: Token <token>` header; query results cached 10 minutes.
- **CourtListener PACER APIs (select-user endpoints, court-ID mapping, field selection):** Context7 library `/websites/courtlistener_help_api_rest_pacer` (https://www.courtlistener.com/help/api/rest/pacer) — HIGH confidence. Key facts: `docket-entries`, `recap-documents`, `recap-query` are select-users-only; court IDs diverge from PACER subdomains (`azb`→`arb`, `cofc`→`uscfc`); `plain_text` is large and should be omitted via Field Selection.
- **CourtListener v4 migration guide (cursor pagination errors):** Context7 library `/websites/courtlistener_help_api_rest` (same, sub-section `v4/migration-guide`) — HIGH confidence. Key fact: changing GET params with an active cursor yields `404 Invalid cursor`.
- **Caffeine W-TinyLFU design (window sizing, CMS 4-bit/8-byte, HashDoS jitter, aging):** Context7 library `/ben-manes/caffeine` (https://github.com/ben-manes/caffeine/wiki/Efficiency and Design pages) — HIGH confidence. Key facts: 4-bit Count-Min Sketch, 8 bytes per element; Window TinyLfu is the production policy; HashDoS mitigation via small probabilistic jitter (<1%) during admission comparison; frequency sketch not initialized below 50% capacity.
- **Caffeine simulator reference:** Context7 confirms the class path `com.github.benmanes.caffeine.cache.simulator.policy.sketch.climbing.HillClimberWindowTinyLfuPolicy` (adaptive) and the non-adaptive `WindowTinyLfuPolicy` — use the latter as the implementation reference.
- **TinyLFU admission policy paper:** Einziger, Friedman, Manes, "TinyLFU: A Highly Efficient Cache Admission Policy," ACM TOS 13(4), Article 16, 2017 — referenced by training data and Caffeine wiki citations. MEDIUM confidence on exact figures; HIGH confidence on the algorithmic description since Caffeine is the reference implementation.
- **SHARDS:** Waldspurger, Park, Garthwaite, Ahmad, "Efficient MRC Construction with SHARDS," FAST 2015 — referenced by the existing project (`src/shards.cpp`, PROCESS.md Phase 3). Paper target is 0.1% MAE at 1% sampling on million-access traces.
- **S3-FIFO:** Yang et al., "FIFO Queues are All You Need for Cache Eviction," SOSP 2023 — referenced for published-results cross-check only. Used as sanity check in PROCESS.md Phase 4.
- **Prior project bugs (historical context, not pitfalls for this phase):** `/Users/mirayu/civicache/PROCESS.md` and `/Users/mirayu/civicache/.planning/codebase/CONCERNS.md` — HIGH confidence, repo-local.

*Pitfalls research: 2026-04-16 by GSD project researcher, sourced from Context7 (CourtListener + Caffeine) and repo-local prior-art.*
