"""Read-only final acceptance analysis of completed samples and Prometheus history."""

from __future__ import annotations

import argparse
import gzip
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from ops.summarize_acceptance import summarize, values


def analyze(path: Path) -> dict[str, Any]:
    raw = gzip.decompress(path.read_bytes()).decode() if path.suffix == ".gz" else path.read_text()
    records = [json.loads(line) for line in raw.splitlines()]
    samples = [r for r in records if "index" in r]
    first, last = samples[0], samples[-1]
    names = sorted({m["name"] for m in last["metrics"]})
    deltas = {
        name: {
            key: value - values(first, name).get(key, 0)
            for key, value in values(last, name).items()
        }
        for name in names
        if name.endswith(("_total", "_sum", "_count", "_bucket"))
    }
    histograms = {}
    for name in names:
        if not name.endswith("_count"):
            continue
        base = name.removesuffix("_count")
        if base + "_sum" not in deltas or base + "_bucket" not in deltas:
            continue
        groups: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for key, value in deltas[base + "_bucket"].items():
            labels = json.loads(key)
            upper = float(labels.pop("le"))
            groups[json.dumps(labels, sort_keys=True)].append((upper, value))
        histograms[base] = {}
        for key, count in deltas[name].items():
            bounds = {}
            for q in (0.5, 0.95, 0.99):
                bound = next((b for b, n in sorted(groups[key]) if n >= count * q), None)
                bounds[str(q)] = bound if bound is not None and bound != float("inf") else None
            histograms[base][key] = {
                "count_delta": count,
                "mean": deltas[base + "_sum"][key] / count if count else None,
                "quantile_bucket_upper": bounds if count else None,
            }
    return {"summary": summarize(path), "counter_deltas": deltas, "histograms": histograms}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Refusing to overwrite evidence")
    result = analyze(args.evidence)
    summary = result["summary"]
    start = datetime.fromisoformat(summary["start_utc"]).timestamp()
    end = datetime.fromisoformat(summary["end_utc"]).timestamp()
    with httpx.Client(
        base_url=f"http://127.0.0.1:{args.port}", trust_env=False, timeout=20
    ) as client:
        response = client.get(
            "/api/v1/query_range",
            params={
                "query": '{__name__=~"crossx_.*|up"}',
                "start": start,
                "end": end,
                "step": 5,
            },
        )
        response.raise_for_status()
        result["prometheus_history"] = response.json()
        for name, endpoint in [
            ("rules", "/api/v1/rules"),
            ("alerts", "/api/v1/alerts"),
            ("targets", "/api/v1/targets"),
        ]:
            response = client.get(endpoint)
            response.raise_for_status()
            result[name] = response.json()
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
