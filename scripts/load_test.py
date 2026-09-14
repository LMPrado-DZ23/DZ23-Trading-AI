from __future__ import annotations

import argparse
import concurrent.futures
import json
import time
import urllib.request


def request(url: str, token: str | None) -> tuple[int, float]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    req = urllib.request.Request(url, headers=headers)
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=10) as response:
        response.read()
        return response.status, time.perf_counter() - started


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only smoke load test for a running DZ23 API")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--token", default=None, help="Bearer token for protected endpoints")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--workers", type=int, default=10)
    args = parser.parse_args()
    urls = [f"{args.base_url}/healthz"] * args.requests
    if args.token:
        urls += [f"{args.base_url}/v1/jobs/health"] * args.requests
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda url: request(url, args.token), urls))
    elapsed = time.perf_counter() - started
    failures = [status for status, _ in results if status != 200]
    latencies = sorted(latency for _, latency in results)
    p95 = latencies[max(0, int(len(latencies) * 0.95) - 1)]
    output = {"requests": len(results), "failures": len(failures), "elapsed_seconds": round(elapsed, 3), "p95_seconds": round(p95, 4), "requests_per_second": round(len(results) / elapsed, 2)}
    print(json.dumps(output, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
