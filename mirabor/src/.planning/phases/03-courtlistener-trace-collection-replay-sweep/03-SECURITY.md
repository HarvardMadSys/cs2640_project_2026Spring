---
phase: 3
slug: courtlistener-trace-collection-replay-sweep
status: verified
threats_open: 0
asvs_level: 1
created: 2026-04-19
---

# Phase 3 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.

**Context:** Phase 3 added a single Python HTTPS client (`scripts/collect_court_trace.py`) that fires ~24K rate-limited `GET` requests against `www.courtlistener.com`, a CSV-reading Python workload-stats script (`scripts/workload_stats_json.py`), and a Makefile parameterization. No network-exposed services, no user-facing API, no shared infrastructure. The simulator remains a single-user local binary. Project is a CS 2640 final-project submission (academic workload, no production deployment planned).

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| collector → `www.courtlistener.com` | HTTPS egress to a single allowlisted host; `Authorization: Token ${COURTLISTENER_API_KEY}` header; response bodies parsed only as opaque byte-length | Public API metadata (dockets/opinions/clusters/courts JSON); size-only recorded |
| `.env` → collector process | `COURTLISTENER_API_KEY` flows via env var into `requests.Session.headers["Authorization"]`; never logged beyond `[:4]+"..."+[-4:]`; never written to disk | Token (sensitive; gitignored `.env`) |
| collector → `traces/court_trace.csv` | Append-only per-row writes of `(timestamp, url_path, byte_length)` tuples; no response body content | Public URL paths + size ints |
| filesystem → `scripts/workload_stats_json.py` | Reads `traces/court_trace.csv` (committed, reviewed); emits `results/court/workload_stats.json` locally | Derived stats (mean/median/p95/α MLE/OHW) |
| filesystem → `./cache_sim` | Reads `traces/court_trace.csv`; writes CSVs/figures to `results/court/` | Same trace CSV; stats outputs |
| developer shell → Makefile | `WORKLOAD=` and `TRACE=` env variables supplied on the `make` command line | Developer-controlled paths (no untrusted input) |

---

## Threat Register

All threats are CLOSED with disposition `accept` per user decision (academic-scope acceptance) during the Phase 3 security audit on 2026-04-19. Mitigation evidence cited where it existed during implementation; future phases may re-audit and elevate to `mitigate` with grep-verified dispositions if scope changes.

| Threat ID | Category | Component | Disposition | Mitigation / Rationale | Status |
|-----------|----------|-----------|-------------|------------------------|--------|
| T-03-01-01 | Tampering/Info-Disclosure | URL construction (SSRF) | accept | Hard allowlist `ALLOWED_HOST = "www.courtlistener.com"` at module level + per-request `urlparse(url).netloc` assertion in `build_request()`; ID-range and court-ID lists are static Python literals — no user input crosses the boundary. Single-developer local collector, no CLI takes URLs from untrusted sources. Academic project, no multi-user exposure. | closed |
| T-03-01-02 | Info-Disclosure | API token leakage | accept | `.env` gitignored (verified: `.gitignore:52`). Only `api_key[:4]+"..."+api_key[-4:]` is ever printed. Token lives in `session.headers["Authorization"]` + env var. Response body is NOT stored (only `len(resp.content)` written to CSV). Local-only collector, no log aggregation. | closed |
| T-03-01-03 | DoS (self-inflicted) | Rate-limit escalation | accept | D-11 429 ramp (Retry-After + `[0, 30, 90]` additions) and 5-consecutive-429 hard-stop with exact FATAL diagnostic present in `scripts/collect_court_trace.py` (verified by Phase 3 verifier). 0.8s + 0-0.4s jitter → ~3,200 req/hr steady-state, well under CourtListener's 5,000/hr authenticated ceiling. Zero 429s observed during the 8h 56m production run. | closed |
| T-03-01-04 | Elevation-of-Privilege | Code-injection via API response | accept | No `eval`, no `exec`, no `subprocess` invoked with response data. Response content used only via `len()` and byte-size arithmetic. `requests` library parses HTTP headers safely. Accepted as originally dispositioned in PLAN.md — academic single-user context; no attack surface from local collector consuming CourtListener-controlled JSON. | closed |
| T-03-02-01 | Info-Disclosure | `results/court/collection_progress.log` leaking token | accept | Only truncated token preview ever printed (`[:4]+"..."+[-4:]`). `results/` is gitignored and `collection_progress.log` is NOT in the D-15 exemption allowlist — confirmed by `git ls-files results/court/collection_progress.log` returning empty. Log is local-only on developer machine; not redistributed. | closed |
| T-03-02-02 | DoS | Sustained 429 escalation during 6-hour run | accept | Same mitigation as T-03-01-03: 5-consec-429 hard-stop + `--resume` flag for controlled continuation. Production run (8h 56m) observed zero 429s. | closed |
| T-03-02-03 | Tampering | Trace CSV corruption during crash mid-write | accept | D-08 per-row `f.flush()` in collector means the trace is durable on the filesystem up to the last flushed row. `--resume` re-counts per-endpoint rows from the existing CSV (D-09). Production run completed cleanly with no interruptions. Partial-write scenario is crash-resilient; never worse than a line-count-truncated but self-consistent CSV. | closed |
| T-03-02-04 | Tampering | Accidental over-exemption in `.gitignore` | accept | The `!` exemptions are path-exact (`!traces/court_trace.csv`, `!results/court/collection_report.txt`) — they do NOT whitelist the parent directory or any sibling file. Any future sibling trace file remains ignored by the `traces/*` and `results/**` rules and would require its own explicit exemption. Accepted as documented in Plan 03-02. | closed |
| T-03-03-01 | Tampering | Trace CSV integrity for stats/sweep | accept | Trace committed in Plan 03-02 at commit `f9b60de` → git blob hash locks content. `workload_stats_json.py` validates the `{timestamp, key, size}` column set before consuming; malformed rows raise `SystemExit`. Local single-developer project; no untrusted tampering vector. | closed |
| T-03-03-02 | Info-Disclosure | Sensitive path leakage in stats JSON | accept | `trace_path` field contains a relative filesystem path (`"traces/court_trace.csv"`) which is already public in the git repo. No secrets, no home directory expansion, no user-controlled strings flow to the JSON. Accepted as originally dispositioned in PLAN.md. | closed |
| T-03-03-03 | Elevation-of-Privilege | Makefile injection via `WORKLOAD` / `TRACE` | accept | `WORKLOAD` and `TRACE` are developer-supplied on the `make` command line (local shell); no untrusted source. Make's shell expansion is confined to the developer's tty. Accepted as originally dispositioned in PLAN.md — single-developer local build context. | closed |

*Status: open · closed*
*Disposition: mitigate (implementation verified by grep/test) · accept (documented risk) · transfer (third-party)*

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-03-01 | T-03-01-01 through T-03-03-03 (all 11) | **Scope acceptance for academic CS 2640 final project.** Phase 3's attack surface is a single-developer local HTTPS client against one allowlisted public API (`www.courtlistener.com`), a CSV-reading Python stats script, and Makefile parameterization. No network-exposed services, no multi-user context, no production deployment. Mitigation evidence for the 7 originally-dispositioned-as-`mitigate` threats exists in the code and was confirmed by the Phase 3 verifier (ALLOWED_HOST, jitter formula, 429 ramp, --resume flag, etc.); user elected bulk-accept per `/gsd-secure-phase` option 2 on the grounds that the academic scope does not warrant a formal auditor pass. If future phases introduce multi-user or production-deployment components, re-audit and upgrade mitigate-dispositions to grep-verified status. | mirabor | 2026-04-19 |

*Accepted risks do not resurface in future audit runs.*

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-04-19 | 11 | 11 | 0 | `/gsd-secure-phase 3` (bulk-accept via user option 2; State B build-from-artifacts path; no auditor agent spawned) |

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer) — 11/11 `accept`
- [x] Accepted risks documented in Accepted Risks Log — AR-03-01 covers all 11
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-04-19
