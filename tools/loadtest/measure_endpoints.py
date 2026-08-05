#!/usr/bin/env python3
"""
Volume-test timing harness (issue #59).

Hits the marking-list endpoint variants that exercise the suspected
non-linear query paths, records p50/p95 latency per endpoint, and appends
one CSV row per endpoint tagged with the current marking count -- so runs
at 10k/50k/100k/500k/1M tiers accumulate into one dataset.

Usage (server already running against the volume-test DB):
    DB_NAME=worldcovers_voltest ./woco runserver 8001   # in another shell
    python3 tools/loadtest/measure_endpoints.py --base-url http://127.0.0.1:8001 \
        --tier-label 100k --out tools/loadtest/results.csv

Stdlib only -- no dependencies.
"""
import argparse
import csv
import json
import pathlib
import statistics
import time
import urllib.request

ENDPOINTS = [
    ("list_default", "/api/v2/markings/?page=1&page_size=10"),
    ("list_year_filtered", "/api/v2/markings/?page=1&page_size=10"
                           "&earliest_use_year_min=1800&latest_use_year_max=1900"),
    ("list_search_text", "/api/v2/markings/?page=1&page_size=10&search=detroit"),
    ("list_default_nocount", "/api/v2/markings/?page=1&page_size=10&include_count=false"),
    ("list_year_nocount", "/api/v2/markings/?page=1&page_size=10&include_count=false"
                          "&earliest_use_year_min=1800&latest_use_year_max=1900"),
    ("town_options", "/api/v2/post-offices/town-options/"),
]


# With DEBUG=False the app sets SECURE_SSL_REDIRECT; SECURE_PROXY_SSL_HEADER
# lets us claim the request is already HTTPS instead of chasing a redirect.
HEADERS = {"X-Forwarded-Proto": "https"}


def fetch(url, timeout):
    req = urllib.request.Request(url, headers=HEADERS)
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return time.perf_counter() - start, resp.status, len(body)


def marking_count(base_url, timeout):
    req = urllib.request.Request(
        f"{base_url}/api/v2/markings/?page=1&page_size=1", headers=HEADERS
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp).get("count", -1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8001")
    ap.add_argument("--iterations", type=int, default=20)
    ap.add_argument("--warmup", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--tier-label", required=True, help="e.g. 10k, 100k, 1M")
    ap.add_argument("--out", default="tools/loadtest/results.csv")
    ap.add_argument("--only", default="",
                    help="Comma-separated endpoint names to run (default: all).")
    args = ap.parse_args()

    try:
        count = marking_count(args.base_url, args.timeout)
    except Exception as exc:
        # At high tiers even the count preflight can exceed the timeout;
        # that is a finding, not a reason to abort the run.
        print(f"count preflight failed ({exc}); continuing with count=-1")
        count = -1
    print(f"tier={args.tier_label} markings={count} base={args.base_url}")

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_header = not out.exists()

    with out.open("a", newline="") as fh:
        writer = csv.writer(fh)
        if write_header:
            writer.writerow(["tier", "markings", "endpoint", "iterations",
                             "p50_ms", "p95_ms", "max_ms", "status", "resp_bytes"])
        selected = {n.strip() for n in args.only.split(",") if n.strip()}
        for name, path in ENDPOINTS:
            if selected and name not in selected:
                continue
            url = args.base_url + path
            timings, status, size = [], None, None
            try:
                for _ in range(args.warmup):
                    fetch(url, args.timeout)
                for _ in range(args.iterations):
                    t, status, size = fetch(url, args.timeout)
                    timings.append(t * 1000)
            except Exception as exc:  # timeouts at high tiers are a *result*
                writer.writerow([args.tier_label, count, name, len(timings),
                                 "", "", "", f"ERROR: {exc}", ""])
                print(f"  {name:24s} ERROR: {exc}")
                continue
            timings.sort()
            p50 = statistics.median(timings)
            p95 = timings[max(0, int(len(timings) * 0.95) - 1)]
            writer.writerow([args.tier_label, count, name, len(timings),
                             f"{p50:.1f}", f"{p95:.1f}", f"{timings[-1]:.1f}",
                             status, size])
            print(f"  {name:24s} p50={p50:8.1f}ms  p95={p95:8.1f}ms  bytes={size}")


if __name__ == "__main__":
    main()
