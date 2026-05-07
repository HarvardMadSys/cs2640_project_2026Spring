#!/usr/bin/env python3
"""
CourtListener v4 API pilot trace collector (Phase 1 sanity check).

Fires a small fixed number of requests (default 200) across 4 planned
endpoints — /dockets/, /opinions/, /clusters/, /courts/ — and emits a
per-endpoint success tally (200s / 404s / 403s / 429s / total).

Phase 1 gate (D-07): each endpoint must hit >=70% success (HTTP 200 +
non-empty body) or the pilot is considered FAILED for that endpoint.
Remediation per CONTEXT.md: narrow the ID range and rerun, or drop the
endpoint if it's fundamentally gated (D-09).

This is throwaway verification code. The production collector lives
at scripts/collect_court_trace.py (created in Phase 3). Do NOT generalize
this script into a shared module (see CONTEXT.md line 93, STACK.md line 32).

Usage:
    # Put COURTLISTENER_API_KEY in .env (see .env.example). Then:
    export $(grep -v '^#' .env | xargs)   # or: source .env
    python3 scripts/pilot_court_trace.py --requests 200 \\
        --output traces/court_pilot.csv \\
        --report results/court/pilot_report.txt
"""

import argparse
import csv
import os
import random
import sys
import time

import requests

BASE_URL = "https://www.courtlistener.com/api/rest/v4"
ALLOWED_HOST = "www.courtlistener.com"  # hard allowlist per PITFALLS C1

# Endpoint specs. ID ranges are loose upper bounds; Phase 1 tunes them
# downward if the pilot fails the 70% gate (D-07).
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

# Rate-limit constants per STACK.md.
BASE_DELAY = 0.8
JITTER = 0.4
MAX_BACKOFF = 300
CONSECUTIVE_429_ABORT = 5            # PITFALLS C2 — abort endpoint after 5 in a row
EARLY_403_SAMPLE = 5                 # PITFALLS C3 / D-09 — all 403 in first N -> drop

SUCCESS_GATE = 0.70


def get_api_key():
    key = os.environ.get("COURTLISTENER_API_KEY")
    if not key:
        print("Error: set COURTLISTENER_API_KEY environment variable "
              "(see .env.example; .env is gitignored)", file=sys.stderr)
        sys.exit(1)
    return key


def pick_endpoint_request(name):
    """Return a request path (relative to BASE_URL) for a random draw
    from the named endpoint. `name` is one of ENDPOINT_ORDER."""
    spec = ENDPOINTS[name]
    if "court_ids" in spec:
        cid = random.choice(spec["court_ids"])
        return spec["path_tpl"].format(court_id=cid)
    lo, hi = spec["id_range"]
    i = random.randint(lo, hi)
    return spec["path_tpl"].format(id=i)


def classify_status(code):
    s = str(code)
    return s if s in STATUS_BUCKETS else "other"


def collect_pilot(api_key, num_requests, output_path, report_path):
    """Run a round-robin pilot across the 4 endpoint families.

    Returns (tally, all_pass) so the caller can use all_pass as an
    exit-code signal (non-zero if any endpoint FAILED the gate).
    """
    # Ensure output dirs exist. results/court is a Phase 1 stub; it may
    # not have been git-committed (results/ is gitignored) — create it.
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)

    session = requests.Session()
    session.headers.update({"Authorization": f"Token {api_key}"})
    session.params = {"format": "json"}

    tally = {ep: {k: 0 for k in STATUS_BUCKETS} for ep in ENDPOINT_ORDER}
    dropped = set()                              # endpoints removed from the mix
    consec_429 = {ep: 0 for ep in ENDPOINT_ORDER}
    early_403 = {ep: 0 for ep in ENDPOINT_ORDER}  # count of consecutive 403s from start
    seen_any_non_403 = {ep: False for ep in ENDPOINT_ORDER}
    consec_fail_global = 0

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "key", "size"])

        # Round-robin order through ENDPOINT_ORDER so each endpoint gets
        # ~num_requests/len(ENDPOINT_ORDER) attempts — gives equal
        # statistical weight to each endpoint (D-08).
        i = 0
        while i < num_requests:
            active = [ep for ep in ENDPOINT_ORDER if ep not in dropped]
            if not active:
                print("All endpoints dropped — aborting pilot.", file=sys.stderr)
                break
            ep = active[i % len(active)]
            i += 1

            path = pick_endpoint_request(ep)
            url = BASE_URL + path
            if ALLOWED_HOST not in url:
                print(f"Host allowlist violation for {url}", file=sys.stderr)
                sys.exit(2)

            ts = int(time.time() * 1000)
            try:
                resp = session.get(url, timeout=30)
            except requests.RequestException as e:
                consec_fail_global += 1
                backoff = min(BASE_DELAY * (2 ** consec_fail_global), MAX_BACKOFF)
                print(f"  {ep} {path}: RequestException: {e}; backing off {backoff:.0f}s")
                time.sleep(backoff)
                continue

            code = resp.status_code
            bucket = classify_status(code)
            tally[ep][bucket] += 1

            # 429 / 5xx -> backoff + track consecutive 429 per endpoint
            if code == 429 or code >= 500:
                consec_fail_global += 1
                if code == 429:
                    consec_429[ep] += 1
                    if consec_429[ep] >= CONSECUTIVE_429_ABORT:
                        print(f"  {ep}: {CONSECUTIVE_429_ABORT} consecutive 429s — "
                              f"dropping endpoint (PITFALLS C2).", file=sys.stderr)
                        dropped.add(ep)
                backoff = min(BASE_DELAY * (2 ** consec_fail_global), MAX_BACKOFF)
                print(f"  {ep} {path}: HTTP {code}; backing off {backoff:.0f}s")
                time.sleep(backoff)
                continue

            # Reset per-endpoint 429 streak + global failure streak on non-429.
            consec_429[ep] = 0
            consec_fail_global = 0

            # Early 403 -> drop endpoint per D-09.
            if code == 403:
                if not seen_any_non_403[ep]:
                    early_403[ep] += 1
                    if early_403[ep] >= EARLY_403_SAMPLE:
                        print(f"  {ep}: {EARLY_403_SAMPLE} consecutive 403s from "
                              f"start — endpoint appears gated; dropping (D-09).",
                              file=sys.stderr)
                        dropped.add(ep)
            else:
                seen_any_non_403[ep] = True

            # Record HTTP-200 row to the trace CSV
            if code == 200:
                body = resp.content
                size = len(body)
                if size > 0:
                    key = path.lstrip("/")
                    writer.writerow([ts, key, size])
                    f.flush()

            # Jittered inter-request delay
            time.sleep(BASE_DELAY + random.uniform(0, JITTER))

    # Build + write the report.
    report_lines = ["=== CourtListener Pilot Summary ==="]
    all_pass = True
    for ep in ENDPOINT_ORDER:
        t = tally[ep]
        total = sum(t.values())
        success_rate = (t["200"] / total) if total else 0.0
        verdict = "PASS" if (total > 0 and success_rate >= SUCCESS_GATE) else "FAIL"
        if verdict == "FAIL":
            all_pass = False
        dropped_note = " (dropped mid-run)" if ep in dropped else ""
        line = (f"  {ep:8s}: 200={t['200']:3d} 404={t['404']:3d} "
                f"403={t['403']:3d} 429={t['429']:3d} other={t['other']:3d} "
                f"total={total:3d} success={success_rate:6.1%} [{verdict}]"
                f"{dropped_note}")
        report_lines.append(line)
    report_lines.append("")
    report_lines.append(f"Gate: each endpoint must hit success >= {SUCCESS_GATE:.0%}")
    report_lines.append(f"Verdict: {'ALL PASS' if all_pass else 'ONE OR MORE FAILED'}")

    report = "\n".join(report_lines) + "\n"
    print("\n" + report)
    with open(report_path, "w") as rf:
        rf.write(report)

    return tally, all_pass


def main():
    parser = argparse.ArgumentParser(
        description="CourtListener pilot trace collector (Phase 1)")
    parser.add_argument("--requests", type=int, default=200,
                        help="Total requests across the 4 endpoints (default: 200)")
    parser.add_argument("--output", default="traces/court_pilot.csv",
                        help="Where to write the pilot trace CSV")
    parser.add_argument("--report", default="results/court/pilot_report.txt",
                        help="Where to write the per-endpoint tally report")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducibility (default: 42)")
    args = parser.parse_args()

    random.seed(args.seed)
    api_key = get_api_key()

    print(f"Running {args.requests}-request pilot across "
          f"{len(ENDPOINT_ORDER)} endpoints; writing {args.output}")
    print(f"API key: {api_key[:4]}...{api_key[-4:]} (len={len(api_key)})")
    _, all_pass = collect_pilot(api_key, args.requests, args.output, args.report)
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
