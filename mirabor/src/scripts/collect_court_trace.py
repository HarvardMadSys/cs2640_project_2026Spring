#!/usr/bin/env python3
"""
CourtListener v4 API production trace collector (Phase 3).

Sibling to scripts/pilot_court_trace.py — this is the overnight / production
collector targeted at 20,000 successful (timestamp, key, size) rows split
equally 25%/25%/25%/25% across 4 endpoint families:
  /dockets/{id}/
  /opinions/{id}/
  /clusters/{id}/
  /courts/{court_id}/

Implements the Phase 3 CONTEXT.md decisions:
  D-01  Hard allowlist for www.courtlistener.com + 80/20 metadata-vs-full
        mix on /opinions/ via ?fields=.
  D-02  Minimal metadata field set: id, absolute_url, type, date_filed, author_id.
  D-03  80/20 applies only to /opinions/; other endpoints fetch default.
  D-04  CSV `size` column = actual len(response.content) regardless of branch.
  D-05  Equal 25% per endpoint = 5000 successful rows each -> 20000 total.
  D-06  First-500-per-endpoint <60% fallback: narrow id_range upper by 0.67
        and log the adjustment to collection_report.txt.
  D-07  Pacing 0.8s + 0-0.4s jitter per request -> ~1s avg, <= 5000/hr.
  D-08  Per-row CSV append with f.flush() after each successful row.
  D-09  --resume flag reads existing CSV, counts per-endpoint successes,
        continues toward the 25/25/25/25 split. Without --resume, refuses
        to overwrite a non-empty existing output CSV.
  D-10  Failed requests (404, 403, 5xx, network errors) are skipped from
        the trace CSV but counted in the per-endpoint tally.
  D-11  429 handling: Retry-After + [0, 30, 90] additional seconds on
        consecutive 429s. After 5 consecutive 429s, hard-stop with the
        exact diagnostic "FATAL: 5 consecutive 429s — token throttled or
        pacing too aggressive. Resume after 1 hour." and exit non-zero.
  D-15  traces/court_trace.csv is the committed default output path.

This is a COPY-MODIFY of scripts/collect_trace.py (Congress) with
ENDPOINTS transplanted verbatim from scripts/pilot_court_trace.py — do
NOT refactor the two collectors into a shared module (PROJECT policy;
Phase 1 CONTEXT line 93, STACK.md §32).

Usage:
    # Put COURTLISTENER_API_KEY in .env (see .env.example). Then:
    set -a; . ./.env; set +a
    python3 scripts/collect_court_trace.py \\
        --output traces/court_trace.csv \\
        --report results/court/collection_report.txt \\
        --target-rows 20000

    # Resume a partial run:
    python3 scripts/collect_court_trace.py --resume \\
        --output traces/court_trace.csv \\
        --report results/court/collection_report.txt
"""

import argparse
import csv
import os
import random
import sys
import time
from urllib.parse import urlparse

import requests

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

BASE_URL = "https://www.courtlistener.com/api/rest/v4"
ALLOWED_HOST = "www.courtlistener.com"  # hard allowlist per PITFALLS C1 (D-01)

# Belt-and-suspenders: if BASE_URL is edited to another host, refuse to run.
# Backs up the per-request urlparse check inside build_request().
# (Threat model T-03-01-01 SSRF mitigation.)
assert urlparse(BASE_URL).netloc == ALLOWED_HOST, (
    f"BASE_URL host {urlparse(BASE_URL).netloc!r} != ALLOWED_HOST "
    f"{ALLOWED_HOST!r}"
)

# Verbatim from scripts/pilot_court_trace.py — Phase 1 tuned these.
# D-06 fallback may narrow an endpoint's id_range at runtime.
ENDPOINTS = {
    "docket": {
        "path_tpl": "/dockets/{id}/",
        "id_range": (1, 80_000_000),
    },
    "opinion": {
        "path_tpl": "/opinions/{id}/",
        "id_range": (1, 15_000_000),
    },
    "cluster": {
        "path_tpl": "/clusters/{id}/",
        "id_range": (1, 12_000_000),
    },
    "court": {
        "path_tpl": "/courts/{court_id}/",
        "court_ids": [
            "scotus", "ca1", "ca2", "ca3", "ca4", "ca5", "ca6", "ca7",
            "ca8", "ca9", "ca10", "ca11", "cadc", "cafc",
            "nysd", "cand", "txnd", "ilnd", "cacd", "caed",
        ],
    },
}
ENDPOINT_ORDER = ["docket", "opinion", "cluster", "court"]
STATUS_BUCKETS = ["200", "404", "403", "429", "other"]

# Pacing (STACK.md §CourtListener + D-07).
BASE_DELAY = 0.8
JITTER = 0.4
MAX_BACKOFF = 300  # cap for network-error exponential backoff

# D-01 / D-02: 80% of /opinions/ calls strip plain_text via ?fields=.
OPINION_METADATA_FRACTION = 0.8
OPINION_METADATA_FIELDS = "id,absolute_url,type,date_filed,author_id"

# D-11 429 ramp (values NOT suggestions). Indexed by (consec_429 - 1), clamped.
RETRY_AFTER_ADDITIONS = [0, 30, 90]  # 1st, 2nd, 3rd consecutive 429
CONSECUTIVE_429_HARD_STOP = 5
HARD_STOP_MSG = (
    "FATAL: 5 consecutive 429s — token throttled or pacing too "
    "aggressive. Resume after 1 hour."
)

# D-05: equal 25% per endpoint, 5000 successful rows each -> 20K total.
PER_ENDPOINT_TARGET = 5000
TOTAL_TARGET = PER_ENDPOINT_TARGET * len(ENDPOINT_ORDER)

# D-06 first-500 fallback (values NOT suggestions).
FALLBACK_PROBE_WINDOW = 500
FALLBACK_SUCCESS_THRESHOLD = 0.60
FALLBACK_NARROW_FACTOR = 0.67

# CSV header shared with traces/court_pilot.csv; MUST NOT drift (Phase 5
# compare_workloads.py relies on the schema).
CSV_HEADER = ["timestamp", "key", "size"]

# Endpoint-to-key-prefix map for --resume row classification (D-09).
# Keys in the CSV are URL paths with the leading slash stripped, e.g.
# "dockets/14942604/". Prefixes must match path_tpl prefixes.
KEY_PREFIX_TO_ENDPOINT = {
    "dockets/": "docket",
    "opinions/": "opinion",
    "clusters/": "cluster",
    "courts/": "court",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_api_key():
    """Read COURTLISTENER_API_KEY from env; exit 1 if missing/empty."""
    key = os.environ.get("COURTLISTENER_API_KEY")
    if not key:
        print(
            "Error: set COURTLISTENER_API_KEY environment variable "
            "(see .env.example; .env is gitignored)",
            file=sys.stderr,
        )
        sys.exit(1)
    return key


def classify_status(code):
    s = str(code)
    return s if s in STATUS_BUCKETS else "other"


def build_request(name, endpoints_state):
    """Construct (path, url, params) for a random draw from endpoint `name`.

    Enforces the hard host allowlist (D-01 / T-03-01-01) per request.
    For /opinions/, applies the 80/20 ?fields= draw (D-01, D-02, D-03).
    """
    spec = endpoints_state[name]
    if "court_ids" in spec:
        cid = random.choice(spec["court_ids"])
        path = spec["path_tpl"].format(court_id=cid)
    else:
        lo, hi = spec["id_range"]
        i = random.randint(lo, hi)
        path = spec["path_tpl"].format(id=i)
    url = BASE_URL + path
    # D-01 hard allowlist — belt-and-suspenders against a malformed path_tpl.
    assert urlparse(url).netloc == ALLOWED_HOST, (
        f"Host allowlist violation: {url}"
    )
    params = {}
    if name == "opinion" and random.random() < OPINION_METADATA_FRACTION:
        # D-01 / D-02: 80% of opinion draws strip plain_text via ?fields=.
        params["fields"] = OPINION_METADATA_FIELDS
    return path, url, params


def endpoint_for_key(key):
    """Classify a CSV row's `key` column back to its endpoint name.

    Used only by --resume. Returns None for unknown prefixes (treated as
    foreign rows and ignored by the resume counter).
    """
    for prefix, ep in KEY_PREFIX_TO_ENDPOINT.items():
        if key.startswith(prefix):
            return ep
    return None


def read_resume_state(output_path):
    """Count per-endpoint successes in an existing output CSV (D-09).

    Returns dict {endpoint_name: count}. Raises if the file is missing
    or has no data rows — that's a usage error the caller surfaces.
    """
    if not os.path.exists(output_path):
        raise FileNotFoundError(output_path)
    counts = {ep: 0 for ep in ENDPOINT_ORDER}
    with open(output_path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != CSV_HEADER:
            raise ValueError(
                f"Unexpected CSV header in {output_path}: {header!r} "
                f"(expected {CSV_HEADER!r})"
            )
        data_rows = 0
        for row in reader:
            if len(row) != 3:
                continue
            key = row[1]
            ep = endpoint_for_key(key)
            if ep is not None:
                counts[ep] += 1
            data_rows += 1
    if data_rows == 0:
        raise ValueError(
            f"{output_path} has a header but no data rows — nothing to resume"
        )
    return counts


def format_tally_line(ep, t, success_count, target):
    """Mirror scripts/pilot_court_trace.py tally format (D-10)."""
    total = sum(t.values())
    rate = (t["200"] / total) if total else 0.0
    # PASS = success_count hit target AND success rate >= fallback threshold.
    gate_passed = (
        success_count >= target
        and rate >= FALLBACK_SUCCESS_THRESHOLD
    )
    verdict = "PASS" if gate_passed else "FAIL"
    return (
        f"  {ep:8s}: 200={t['200']:4d} 404={t['404']:4d} "
        f"403={t['403']:4d} 429={t['429']:4d} other={t['other']:4d} "
        f"total={total:4d} success={rate:6.1%} [{verdict}]"
    )


def write_report(report_path, tally, success_count, per_endpoint_target,
                 total_target, runtime_s, fallback_log, hard_stopped=False):
    """Write the collection report file (D-10 + D-06 fallback lines)."""
    os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
    lines = ["=== CourtListener Trace Collection Summary ==="]
    for ep in ENDPOINT_ORDER:
        lines.append(
            format_tally_line(ep, tally[ep], success_count[ep],
                              per_endpoint_target[ep])
        )
    achieved = sum(success_count.values())
    hours = int(runtime_s // 3600)
    minutes = int((runtime_s % 3600) // 60)
    seconds = int(runtime_s % 60)
    if hours:
        runtime_str = f"{hours}h {minutes:02d}m"
    elif minutes:
        runtime_str = f"{minutes}m {seconds:02d}s"
    else:
        runtime_str = f"{seconds}s"
    lines.append("")
    lines.append(
        f"Target rows: {total_target}   Achieved: {achieved}   "
        f"Runtime: {runtime_str}"
    )
    if hard_stopped:
        lines.append("")
        lines.append(f"HARD-STOP: {HARD_STOP_MSG}")
    if fallback_log:
        lines.append("")
        lines.append("Fallback adjustments (D-06):")
        for msg in fallback_log:
            lines.append(msg)

    report = "\n".join(lines) + "\n"
    with open(report_path, "w") as rf:
        rf.write(report)
    return report


# ---------------------------------------------------------------------------
# Main collection loop
# ---------------------------------------------------------------------------

def collect(api_key, output_path, report_path, target_rows, resume):
    """Run the production collection loop. Returns exit code."""
    # Compute the per-endpoint target split. Total target is passed via CLI
    # and typically equals PER_ENDPOINT_TARGET * 4 = 20000 (D-05). For the
    # smoke path (--target-rows 10), we split as evenly as possible so a
    # 10-row run touches every endpoint.
    base = target_rows // len(ENDPOINT_ORDER)
    remainder = target_rows % len(ENDPOINT_ORDER)
    per_endpoint_target = {}
    for idx, ep in enumerate(ENDPOINT_ORDER):
        per_endpoint_target[ep] = base + (1 if idx < remainder else 0)
    total_target = sum(per_endpoint_target.values())

    # Per-endpoint state (deep-ish copy so D-06 narrowing doesn't mutate
    # the module-level ENDPOINTS dict).
    endpoints_state = {
        ep: {
            k: (list(v) if k == "court_ids" else v)
            for k, v in spec.items()
        }
        for ep, spec in ENDPOINTS.items()
    }
    # id_range must stay a tuple of ints in our runtime state — materialize
    # it as a mutable form (list-style) only where we need to write.
    for ep, spec in endpoints_state.items():
        if "id_range" in spec:
            spec["id_range"] = tuple(spec["id_range"])

    tally = {ep: {k: 0 for k in STATUS_BUCKETS} for ep in ENDPOINT_ORDER}
    consec_429 = {ep: 0 for ep in ENDPOINT_ORDER}
    success_count = {ep: 0 for ep in ENDPOINT_ORDER}
    fallback_triggered = {ep: False for ep in ENDPOINT_ORDER}
    fallback_log = []
    consec_network_failures = 0

    # --resume handling (D-09).
    if resume:
        try:
            existing = read_resume_state(output_path)
        except (FileNotFoundError, ValueError) as e:
            print(
                f"Error: --resume requires a well-formed existing CSV at "
                f"{output_path}: {e}",
                file=sys.stderr,
            )
            return 1
        for ep, n in existing.items():
            success_count[ep] = n
        resumed_total = sum(existing.values())
        print(
            f"--resume: counted {resumed_total} existing successful rows "
            f"across endpoints: " +
            ", ".join(f"{ep}={existing[ep]}" for ep in ENDPOINT_ORDER)
        )
        csv_mode = "a"
        write_header = False
    else:
        # Fresh-run safety: refuse to overwrite a non-empty CSV (D-09).
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            print(
                f"Error: output CSV {output_path} exists and is non-empty. "
                f"Use --resume to continue, or delete/move the file.",
                file=sys.stderr,
            )
            return 1
        csv_mode = "w"
        write_header = True

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)

    session = requests.Session()
    session.headers.update({"Authorization": f"Token {api_key}"})
    session.params = {"format": "json"}

    start_time = time.time()
    rr_idx = 0  # round-robin cursor over ENDPOINT_ORDER

    with open(output_path, csv_mode, newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(CSV_HEADER)
            f.flush()

        # Main loop: keep issuing requests until every endpoint is at target.
        while sum(success_count.values()) < total_target:
            # Pick the next endpoint that still needs rows.
            active = [
                ep for ep in ENDPOINT_ORDER
                if success_count[ep] < per_endpoint_target[ep]
            ]
            if not active:
                break
            ep = active[rr_idx % len(active)]
            rr_idx += 1

            path, url, params = build_request(ep, endpoints_state)
            ts = int(time.time() * 1000)

            try:
                resp = session.get(url, params=params, timeout=30)
            except requests.RequestException as e:
                consec_network_failures += 1
                backoff = min(
                    BASE_DELAY * (2 ** consec_network_failures),
                    MAX_BACKOFF,
                )
                print(
                    f"  {ep} {path}: RequestException: {e}; "
                    f"backing off {backoff:.0f}s "
                    f"({consec_network_failures} consecutive failures)"
                )
                time.sleep(backoff)
                continue

            code = resp.status_code
            bucket = classify_status(code)
            tally[ep][bucket] += 1

            # D-11 429 ramp + 5-consecutive hard-stop.
            if code == 429:
                consec_429[ep] += 1
                # Total consecutive 429s across all endpoints would also be
                # meaningful, but D-11 scopes it per-endpoint. Using per-ep
                # counter here; any non-429 on this endpoint resets it below.
                if consec_429[ep] >= CONSECUTIVE_429_HARD_STOP:
                    print(HARD_STOP_MSG, file=sys.stderr)
                    # Flush the current state to the report before exiting so
                    # operators can see per-endpoint progress at the moment
                    # of throttle.
                    runtime_s = time.time() - start_time
                    write_report(
                        report_path, tally, success_count,
                        per_endpoint_target, total_target, runtime_s,
                        fallback_log, hard_stopped=True,
                    )
                    return 2

                retry_after_hdr = resp.headers.get("Retry-After", "")
                try:
                    retry_after = int(retry_after_hdr) if retry_after_hdr else 60
                except ValueError:
                    retry_after = 60
                retry_after = min(retry_after, 120)
                # Add the ramp extra. For 4th+ consecutive 429s (before the
                # hard-stop triggers at 5), reuse the last bucket (+90).
                idx = min(consec_429[ep] - 1, len(RETRY_AFTER_ADDITIONS) - 1)
                sleep_s = retry_after + RETRY_AFTER_ADDITIONS[idx]
                print(
                    f"  {ep} {path}: HTTP 429 "
                    f"(consec={consec_429[ep]}); sleeping {sleep_s}s "
                    f"(Retry-After={retry_after}, ramp=+"
                    f"{RETRY_AFTER_ADDITIONS[idx]})"
                )
                time.sleep(sleep_s)
                continue

            # Any non-429 response resets both 429 and network failure streaks.
            consec_429[ep] = 0
            consec_network_failures = 0

            # D-06 first-500 fallback: evaluated AFTER the 500th response
            # (i.e., once total >= 500). Fires once per endpoint.
            if (
                not fallback_triggered[ep]
                and sum(tally[ep].values()) >= FALLBACK_PROBE_WINDOW
            ):
                total_for_ep = sum(tally[ep].values())
                success_rate = tally[ep]["200"] / total_for_ep
                if success_rate < FALLBACK_SUCCESS_THRESHOLD:
                    if "id_range" in endpoints_state[ep]:
                        lo, hi = endpoints_state[ep]["id_range"]
                        hi_new = max(lo + 1, int(hi * FALLBACK_NARROW_FACTOR))
                        endpoints_state[ep]["id_range"] = (lo, hi_new)
                        msg = (
                            f"  [FALLBACK] {ep}: first "
                            f"{FALLBACK_PROBE_WINDOW} at {success_rate:.1%} "
                            f"— narrowed id_range to ({lo}, {hi_new}); "
                            f"counters reset"
                        )
                    else:
                        # court endpoint has a static court_ids list — nothing
                        # sensible to narrow. Log anomaly but don't mutate.
                        msg = (
                            f"  [FALLBACK] {ep}: first "
                            f"{FALLBACK_PROBE_WINDOW} at {success_rate:.1%} "
                            f"— court_ids list is curated; no narrowing"
                        )
                    print(msg)
                    fallback_log.append(msg)
                    # Reset counters for this endpoint so the new range gets
                    # a fresh window toward the per-endpoint target.
                    tally[ep] = {k: 0 for k in STATUS_BUCKETS}
                    success_count[ep] = 0
                fallback_triggered[ep] = True

            # D-10 + D-08: record successful rows (HTTP 200 + non-empty body)
            # only. 404/403/5xx counted in tally but skipped from CSV.
            if code == 200:
                body = resp.content
                size = len(body)  # D-04: actual byte length, all branches
                if size > 0:
                    key = path.lstrip("/")  # matches pilot CSV convention
                    writer.writerow([ts, key, size])
                    f.flush()  # D-08: never lose rows on crash mid-run
                    success_count[ep] += 1

                    achieved = sum(success_count.values())
                    if achieved % 100 == 0:
                        elapsed = time.time() - start_time
                        print(
                            f"  [{achieved}/{total_target}] "
                            f"{elapsed:.0f}s elapsed — last: {key} "
                            f"({size} bytes)"
                        )
                    if achieved % 1000 == 0:
                        # Periodic per-endpoint tally dump
                        # (mirrors scripts/collect_trace.py cadence).
                        print("  --- per-endpoint progress ---")
                        for e2 in ENDPOINT_ORDER:
                            t = tally[e2]
                            total = sum(t.values())
                            rate = (t["200"] / total) if total else 0.0
                            print(
                                f"    {e2:8s}: success="
                                f"{success_count[e2]}/"
                                f"{per_endpoint_target[e2]} "
                                f"(issued={total}, rate={rate:.1%})"
                            )

            # Pacing per D-07: 0.8s + 0-0.4s jitter.
            time.sleep(BASE_DELAY + random.uniform(0, JITTER))

    runtime_s = time.time() - start_time
    report = write_report(
        report_path, tally, success_count, per_endpoint_target,
        total_target, runtime_s, fallback_log, hard_stopped=False,
    )
    print("\n" + report)
    print(
        f"Done. Collected {sum(success_count.values())} successful rows "
        f"in {runtime_s:.0f}s; trace -> {output_path}, report -> "
        f"{report_path}"
    )
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="CourtListener v4 production trace collector (Phase 3)",
    )
    parser.add_argument(
        "--output",
        default="traces/court_trace.csv",
        help="Output CSV path (default: traces/court_trace.csv per D-15)",
    )
    parser.add_argument(
        "--report",
        default="results/court/collection_report.txt",
        help="Per-endpoint tally report path "
             "(default: results/court/collection_report.txt)",
    )
    parser.add_argument(
        "--target-rows",
        type=int,
        default=TOTAL_TARGET,
        help=f"Total successful rows to collect "
             f"(default: {TOTAL_TARGET}; split equally across "
             f"{len(ENDPOINT_ORDER)} endpoints internally per D-05)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Read existing CSV and continue toward the 25/25/25/25 split "
             "(D-09). Without --resume the collector refuses to overwrite "
             "a non-empty output CSV.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    args = parser.parse_args()

    random.seed(args.seed)
    api_key = get_api_key()

    print(
        f"CourtListener collector: target={args.target_rows} rows, "
        f"output={args.output}, report={args.report}, "
        f"resume={args.resume}, seed={args.seed}"
    )
    # Only ever print a token fingerprint — never the token itself.
    print(
        f"API key: {api_key[:4]}...{api_key[-4:]} (len={len(api_key)})"
    )
    sys.exit(
        collect(api_key, args.output, args.report, args.target_rows,
                args.resume)
    )


if __name__ == "__main__":
    main()
