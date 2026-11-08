"""Capture five-second Prometheus history for the completed observation window."""

from __future__ import annotations

import argparse
import gzip
import json
from datetime import datetime
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--port", type=int, default=19091)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Refusing to overwrite existing evidence")
    contents = (
        gzip.decompress(args.evidence.read_bytes()).decode()
        if args.evidence.suffix == ".gz"
        else args.evidence.read_text()
    )
    samples = [json.loads(line) for line in contents.splitlines() if '"index"' in line]
    start = datetime.fromisoformat(samples[0]["timestamp"]).timestamp()
    end = datetime.fromisoformat(samples[-1]["timestamp"]).timestamp()
    queries = {
        "health": '{__name__=~"up|crossx_ready|crossx_book_synchronized|crossx_queue_depth"}',
        "receive_age_seconds": "time() - crossx_last_book_timestamp_seconds",
        "feed_errors": '{__name__=~"crossx_reconnects|crossx_integrity_errors"}',
    }
    results = {"start_unix": start, "end_unix": end, "step_seconds": 5, "queries": {}}
    with httpx.Client(timeout=15, trust_env=False) as client:
        for name, query in queries.items():
            response = client.get(
                f"http://127.0.0.1:{args.port}/api/v1/query_range",
                params={"query": query, "start": start, "end": end, "step": 5},
            )
            response.raise_for_status()
            results["queries"][name] = response.json()
    args.out.write_text(json.dumps(results, indent=2) + "\n")
    print(
        json.dumps(
            {
                "window_seconds": end - start,
                "series": {
                    key: len(value["data"]["result"]) for key, value in results["queries"].items()
                },
            }
        )
    )


if __name__ == "__main__":
    main()
