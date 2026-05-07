# External Integrations

**Analysis Date:** 2026-04-16

## APIs & External Services

**Public Data APIs:**
- **Congress.gov API v3** — sole external service; used to collect a real request trace for cache-policy evaluation.
  - Base URL: `https://api.congress.gov/v3` (declared as `BASE_URL` in `scripts/collect_trace.py:24`).
  - SDK/Client: none. Plain `requests.Session` with `session.params = {"api_key": api_key, "format": "json"}` set once, so every request carries the key and requests JSON. See `scripts/collect_trace.py:117`.
  - Auth: query-string `api_key` parameter sourced from the `CONGRESS_API_KEY` environment variable (`scripts/collect_trace.py:51-55`).
  - Endpoints queried (templates defined in `ENDPOINTS` dict at `scripts/collect_trace.py:27-43`):
    - `GET /bill/{congress}/{type}/{number}` — 60% of requests. `congress` ∈ 93..119, `type` ∈ `hr, s, hjres, sjres, hconres, sconres, hres, sres`.
    - `GET /amendment/{congress}/{type}/{number}` — 25% of requests. `type` ∈ `hamdt, samdt`.
    - `GET /roll-call-vote/{congress}/{chamber}/{session}/{number}` — 15% of requests. `chamber` ∈ `house, senate`, `session` ∈ `{1, 2}`.
  - Rate limiting: hand-rolled in `scripts/collect_trace.py:119-170`. Base delay 1.2s (just above the documented 1 req/s limit), plus random jitter 0..0.8s. On HTTP 429 or 5xx, exponential backoff `min(base_delay * 2^consecutive_failures, 300)` seconds. Non-200 responses (e.g. 404, 403) are skipped without being written to the trace.
  - Popularity skew: 60% of bill/amendment requests target the current (119th) Congress; configured via `CURRENT_CONGRESS_WEIGHT = 0.6` at `scripts/collect_trace.py:47`.
  - Workload weights: `generate_request()` at `scripts/collect_trace.py:92-100` picks endpoint type with the 60/25/15 split above.

**No other external HTTP services.** No `openai`, `anthropic`, `stripe`, `supabase`, `aws-sdk`, `google-*`, or similar SDK imports exist in the codebase.

## Data Storage

**Databases:**
- None. The project has no database connection, ORM, client library, or connection string.

**File Storage:**
- Local filesystem only.
  - Input traces: `traces/*.csv` (gitignored). Created by `scripts/collect_trace.py` with CSV header `timestamp,key,size` (`scripts/collect_trace.py:113`) and parsed in C++ by `load_trace()` in `src/trace_gen.cpp:31-50`.
  - Simulation outputs: `results/*.csv` (gitignored). Written by `src/main.cpp`:
    - `results/mrc.csv` — columns `cache_frac,cache_size_bytes,policy,miss_ratio,byte_miss_ratio` (`src/main.cpp:167-199`).
    - `results/alpha_sensitivity.csv` — columns `alpha,policy,miss_ratio,byte_miss_ratio` (`src/main.cpp:205-237`).
    - `results/one_hit_wonder.csv` — columns `window_frac,ohw_ratio` (`src/main.cpp:242-255`).
    - `results/shards_mrc.csv` — columns `sampling_rate,cache_size_objects,miss_ratio` (`src/main.cpp:265-285`).
    - `results/exact_mrc.csv` — columns `cache_size_objects,miss_ratio` (`src/main.cpp:296-303`, only when `--shards-exact`).
    - `results/shards_error.csv` — columns `sampling_rate,mae,max_abs_error,num_points` (`src/main.cpp:306-334`).
  - Figures: `results/figures/*.pdf` produced by `scripts/plot_results.py` via `fig.savefig(out, bbox_inches="tight")` with `matplotlib` `Agg` backend.

**Caching:**
- The project *studies* caching; it does not *use* an external cache. In-memory cache policies (`LRUCache`, `FIFOCache`, `CLOCKCache`, `S3FIFOCache`, `SIEVECache`) live entirely inside the `cache_sim` process and are defined in `include/cache.h`.

## Authentication & Identity

**Auth Provider:**
- None for the simulator. The only authenticated surface is the outbound Congress.gov call from `scripts/collect_trace.py`, which authenticates via an API key in the query string (`session.params = {"api_key": api_key, ...}` at `scripts/collect_trace.py:117`).
- No user accounts, sessions, OAuth, JWTs, or identity providers.

## Monitoring & Observability

**Error Tracking:**
- None. No Sentry, Rollbar, Datadog, Honeycomb, or OpenTelemetry integration.

**Logs:**
- `stdout`/`stderr` only.
  - Simulator: human-readable status tables and CSV-path confirmations via `std::cout` and `std::cerr` in `src/main.cpp`.
  - Collector: progress messages via `print(...)` to stdout, errors to stderr. Persistent run log observed at `results/collect.log` (user-redirected, not managed by code).

## CI/CD & Deployment

**Hosting:**
- Not applicable — no deployed service.

**CI Pipeline:**
- None. No `.github/workflows/`, `.gitlab-ci.yml`, `.circleci/`, Travis, Jenkins, or Buildkite configuration present.

## Environment Configuration

**Required env vars:**
- `CONGRESS_API_KEY` — required by `scripts/collect_trace.py` only. Not consumed by `cache_sim` (the C++ simulator reads no environment variables).

**Secrets location:**
- `.env` file at repo root exists and is listed in `.gitignore` (entry `.env`). Contents are not read by any script in this repo — the user must `export CONGRESS_API_KEY` manually before running the collector (see `README.md:64`).

## Webhooks & Callbacks

**Incoming:**
- None. The project exposes no HTTP server, no listener, and no public endpoints.

**Outgoing:**
- Outbound HTTP `GET` requests to `https://api.congress.gov/v3/...` from `scripts/collect_trace.py`. No webhooks, callbacks, or push integrations.

---

*Integration audit: 2026-04-16*
