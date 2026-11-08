"""Derive an auditable summary from flushed acceptance samples; no service mutations."""

from __future__ import annotations

import argparse
import gzip
import json
import math
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any


def values(record: dict[str, Any], name: str) -> dict[str, float]:
    return {
        json.dumps(item["labels"], sort_keys=True): float(item["value"])
        for item in record.get("metrics", [])
        if item["name"] == name
    }


def scalar(record: dict[str, Any], name: str) -> float:
    items = values(record, name)
    return next(iter(items.values()))


def memory_bytes(value: str) -> float:
    amount, unit = re.fullmatch(r"([\d.]+)([A-Za-z]+)", value.strip()).groups()  # type: ignore[union-attr]
    return (
        float(amount)
        * {
            "B": 1,
            "kB": 1000,
            "KB": 1000,
            "KiB": 1024,
            "MB": 1000**2,
            "MiB": 1024**2,
            "GB": 1000**3,
            "GiB": 1024**3,
        }[unit]
    )


def summarize(path: Path) -> dict[str, Any]:
    contents = (
        gzip.decompress(path.read_bytes()).decode() if path.suffix == ".gz" else path.read_text()
    )
    records = [json.loads(line) for line in contents.splitlines()]
    samples = [record for record in records if "index" in record]
    if len(samples) < 2:
        raise ValueError("Need at least two samples")
    first, last = samples[0], samples[-1]
    elapsed = last["elapsed_seconds"] - first["elapsed_seconds"]
    errors = [
        {"index": sample["index"], "part": key, "error": part["error"]}
        for sample in samples
        for key, part in sample.items()
        if isinstance(part, dict) and "error" in part
    ]
    if errors:
        return {"completed_seconds": elapsed, "errors": errors, "measurement_complete": False}
    counter_deltas = {}
    for name in ("crossx_books_total", "crossx_reconnects", "crossx_integrity_errors"):
        start, end = values(first, name), values(last, name)
        counter_deltas[name] = {key: value - start.get(key, 0) for key, value in end.items()}
    pairs = {}
    for item in last["database"]["pairs"]:
        key = f"{item['exchange']} {item['symbol']}"
        initial = next(
            p
            for p in first["database"]["pairs"]
            if p["exchange"] == item["exchange"] and p["symbol"] == item["symbol"]
        )
        pairs[key] = {
            "initial": initial["observations"],
            "final": item["observations"],
            "delta": item["observations"] - initial["observations"],
            "per_second": (item["observations"] - initial["observations"]) / elapsed,
        }
    db_delta = {
        key: int(last["database"][key]) - int(first["database"][key])
        for key in ("rows", "book_bytes", "database_bytes", "wal_bytes", "transactions_committed")
    }
    write_count = scalar(last, "crossx_persist_seconds_count") - scalar(
        first, "crossx_persist_seconds_count"
    )
    latency_sum = scalar(last, "crossx_persist_seconds_sum") - scalar(
        first, "crossx_persist_seconds_sum"
    )
    bucket_start = values(first, "crossx_persist_seconds_bucket")
    buckets = sorted(
        (float(json.loads(key)["le"]), value - bucket_start[key])
        for key, value in values(last, "crossx_persist_seconds_bucket").items()
    )
    quantile_bounds = {}
    for q in (0.5, 0.95, 0.99):
        bound = next(upper for upper, cumulative in buckets if cumulative >= q * write_count)
        quantile_bounds[str(q)] = bound if math.isfinite(bound) else None
    resources: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        for item in sample["resources"]:
            resources[item["Name"]].append(item)
    resource_summary = {}
    for name, items in resources.items():
        cpu = [float(item["CPUPerc"].rstrip("%")) for item in items]
        mem = [memory_bytes(item["MemUsage"].split("/")[0]) for item in items]
        resource_summary[name] = {
            "samples": len(items),
            "cpu_mean_percent": mean(cpu),
            "cpu_max_percent": max(cpu),
            "memory_first_bytes": mem[0],
            "memory_last_bytes": mem[-1],
            "memory_mean_bytes": mean(mem),
            "memory_max_bytes": max(mem),
            "pids_max": max(int(item["PIDs"]) for item in items),
        }
    container_summary = []
    for item in last["containers"]:
        related = [
            c for sample in samples for c in sample["containers"] if c["service"] == item["service"]
        ]
        container_summary.append(
            {
                "service": item["service"],
                "ids": sorted({c["id"] for c in related}),
                "start_times": sorted({c["started_at"] for c in related}),
                "restart_counts": sorted({c["restart_count"] for c in related}),
                "states": sorted({c["state"] for c in related}),
            }
        )
    monitoring = [sample["monitoring"] for sample in samples if "monitoring" in sample]
    ages = defaultdict(list)
    for sample in samples:
        observed = datetime.fromisoformat(sample["timestamp"]).timestamp()
        for key, value in values(sample, "crossx_last_book_timestamp_seconds").items():
            ages[key].append(max(0.0, observed - value))
    throughputs = [
        (b["database"]["rows"] - a["database"]["rows"])
        / (b["elapsed_seconds"] - a["elapsed_seconds"])
        for a, b in zip(samples, samples[1:], strict=False)
    ]
    return {
        "measurement_complete": records[-1].get("event") == "end" and elapsed >= 900,
        "start_utc": first["timestamp"],
        "end_utc": last["timestamp"],
        "completed_seconds": elapsed,
        "samples": len(samples),
        "errors": errors,
        "collection_seconds_max": max(sample["collection_seconds"] for sample in samples),
        "sample_gap_seconds_max": max(
            b["elapsed_seconds"] - a["elapsed_seconds"]
            for a, b in zip(samples, samples[1:], strict=False)
        ),
        "pairs_persisted": pairs,
        "metric_deltas": counter_deltas,
        "database_initial": first["database"],
        "database_final": last["database"],
        "database_deltas": db_delta,
        "write_rows_per_second": db_delta["rows"] / elapsed,
        "interval_write_rows_per_second_min": min(throughputs),
        "interval_write_rows_per_second_max": max(throughputs),
        "book_storage_bytes_per_observation": db_delta["book_bytes"] / db_delta["rows"],
        "book_storage_growth_bytes_per_day_extrapolated": db_delta["book_bytes"] / elapsed * 86400,
        "wal_growth_bytes_per_day_extrapolated": db_delta["wal_bytes"] / elapsed * 86400,
        "opportunities_min": min(sample["database"]["opportunities"] for sample in samples),
        "opportunities_max": max(sample["database"]["opportunities"] for sample in samples),
        "ready_values": sorted(
            {v for sample in samples for v in values(sample, "crossx_ready").values()}
        ),
        "synchronization_values": sorted(
            {v for sample in samples for v in values(sample, "crossx_book_synchronized").values()}
        ),
        "queue_depth_max_sampled": max(scalar(sample, "crossx_queue_depth") for sample in samples),
        "receive_age_seconds_max_sampled": {key: max(items) for key, items in ages.items()},
        "persistence_calls": write_count,
        "persistence_seconds_mean": latency_sum / write_count,
        "persistence_quantile_bucket_upper_seconds": quantile_bounds,
        "resources": resource_summary,
        "containers": container_summary,
        "monitoring_checks": len(monitoring),
        "prometheus_target_health": sorted(
            {
                t["health"]
                for entry in monitoring
                for t in entry["prometheus_targets"]["data"]["activeTargets"]
            }
        ),
        "grafana_database_health": sorted(
            {entry["grafana_health"]["database"] for entry in monitoring}
        ),
        "grafana_datasource_health": sorted(
            {entry["grafana_datasource"]["status"] for entry in monitoring}
        ),
        "grafana_panel_counts": sorted(
            {len(entry["grafana_dashboard"]["panels"]) for entry in monitoring}
        ),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.evidence), indent=2, allow_nan=False))
