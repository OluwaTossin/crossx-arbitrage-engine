"""Read-only timed sampler for an explicitly isolated acceptance Compose project.

No start/stop/delete operations are performed. Output is flushed after each sample.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from prometheus_client.parser import text_string_to_metric_families

SQL = """
SELECT json_build_object(
 'pairs', (SELECT json_agg(t) FROM (
   SELECT exchange,symbol,count(*) AS observations,
          min(received_at) AS first_received,max(received_at) AS last_received
   FROM book_observations GROUP BY exchange,symbol ORDER BY exchange,symbol) t),
 'rows', (SELECT count(*) FROM book_observations),
 'opportunities', (SELECT count(*) FROM opportunities),
 'book_bytes', pg_total_relation_size('book_observations'),
 'database_bytes', pg_database_size(current_database()),
 'wal_bytes', (SELECT wal_bytes::text FROM pg_stat_wal),
 'transactions_committed', (SELECT xact_commit FROM pg_stat_database
                            WHERE datname=current_database())
);
"""


def utc() -> str:
    return datetime.now(UTC).isoformat()


def command(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=12, check=True)
    return result.stdout.strip()


def containers(project: str) -> list[dict[str, Any]]:
    ids = command(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            "{{.ID}}",
        ]
    ).split()
    if not ids:
        raise RuntimeError(f"No containers for {project}")
    items = json.loads(command(["docker", "inspect", *ids]))
    return [
        {
            "id": item["Id"],
            "name": item["Name"],
            "image": item["Image"],
            "service": item["Config"]["Labels"].get("com.docker.compose.service"),
            "state": item["State"]["Status"],
            "started_at": item["State"]["StartedAt"],
            "restart_count": item["RestartCount"],
            "exit_code": item["State"]["ExitCode"],
            "networks": sorted(item["NetworkSettings"]["Networks"]),
            "volumes": [mount.get("Name", mount["Source"]) for mount in item["Mounts"]],
            "ports": item["HostConfig"]["PortBindings"],
        }
        for item in items
    ]


def metrics(port: int) -> list[dict[str, Any]]:
    response = httpx.get(f"http://127.0.0.1:{port}/metrics", timeout=8, trust_env=False)
    response.raise_for_status()
    return [
        {"name": sample.name, "labels": sample.labels, "value": sample.value}
        for family in text_string_to_metric_families(response.text)
        for sample in family.samples
        if sample.name.startswith("crossx_")
    ]


def monitoring(prom_port: int, grafana_port: int) -> dict[str, Any]:
    paths = {
        "prometheus_targets": f"http://127.0.0.1:{prom_port}/api/v1/targets",
        "grafana_health": f"http://127.0.0.1:{grafana_port}/api/health",
        "grafana_dashboard": (f"http://127.0.0.1:{grafana_port}/api/dashboards/uid/crossx-phase1"),
        "grafana_datasource": (
            f"http://127.0.0.1:{grafana_port}/api/datasources/uid/prometheus/health"
        ),
    }
    results = {}
    with httpx.Client(timeout=8, trust_env=False) as client:
        for key, url in paths.items():
            response = client.get(url)
            response.raise_for_status()
            results[key] = response.json()
    # Keep provisioning evidence concise while recording the actual panel identities.
    dashboard = results["grafana_dashboard"]
    results["grafana_dashboard"] = {
        "provisioned": dashboard["meta"].get("provisioned"),
        "uid": dashboard["dashboard"]["uid"],
        "panels": [{"id": p["id"], "title": p["title"]} for p in dashboard["dashboard"]["panels"]],
    }
    return results


def sample(args: argparse.Namespace, started: float, index: int) -> dict[str, Any]:
    compose = [
        "docker",
        "compose",
        "--project-name",
        args.project,
        "--file",
        "ops/compose.acceptance.yaml",
    ]
    record: dict[str, Any] = {
        "index": index,
        "timestamp": utc(),
        "elapsed_seconds": time.monotonic() - started,
    }
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {
            "metrics": pool.submit(metrics, args.app_port),
            "database": pool.submit(
                command,
                [
                    *compose,
                    "exec",
                    "-T",
                    "postgres",
                    "psql",
                    "-U",
                    "crossx",
                    "-d",
                    "crossx",
                    "-At",
                    "-c",
                    SQL,
                ],
            ),
            "containers": pool.submit(containers, args.project),
            "resources": pool.submit(
                command,
                ["docker", "stats", "--no-stream", "--format", "{{json .}}", *args.container_ids],
            ),
        }
        for key, future in pending.items():
            try:
                value = future.result()
                if key == "database":
                    value = json.loads(value)
                elif key == "resources":
                    value = [json.loads(line) for line in value.splitlines()]
                record[key] = value
            except Exception as exc:
                record[key] = {"error": f"{type(exc).__name__}: {exc}"}
    if index % 4 == 0:
        try:
            record["monitoring"] = monitoring(args.prom_port, args.grafana_port)
        except Exception as exc:
            record["monitoring"] = {"error": f"{type(exc).__name__}: {exc}"}
    record["collection_seconds"] = time.monotonic() - started - record["elapsed_seconds"]
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--duration", type=float, default=900)
    parser.add_argument("--interval", type=float, default=15)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--app-port", type=int, default=18080)
    parser.add_argument("--prom-port", type=int, default=19091)
    parser.add_argument("--grafana-port", type=int, default=13080)
    args = parser.parse_args()
    if not args.project.startswith("crossx-acceptance-") or not all(
        math.isfinite(value) and value >= 1 for value in (args.duration, args.interval)
    ):
        parser.error("Use an explicit crossx-acceptance-* project and positive duration/interval")
    if args.out.exists():
        parser.error("Refusing to overwrite existing evidence")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    initial_containers = containers(args.project)
    args.container_ids = [item["id"] for item in initial_containers]
    started = time.monotonic()
    with args.out.open("x") as output:
        output.write(
            json.dumps(
                {
                    "event": "start",
                    "timestamp": utc(),
                    "duration_seconds": args.duration,
                    "interval_seconds": args.interval,
                    "project": args.project,
                    "pid": os.getpid(),
                    "containers": initial_containers,
                }
            )
            + "\n"
        )
        output.flush()
        index = 0
        while True:
            record = sample(args, started, index)
            output.write(json.dumps(record) + "\n")
            output.flush()
            db = record.get("database", {})
            print(
                json.dumps(
                    {
                        "sample": index,
                        "elapsed_seconds": round(record["elapsed_seconds"], 2),
                        "rows": db.get("rows"),
                        "opportunities": db.get("opportunities"),
                        "sample_seconds": round(record["collection_seconds"], 2),
                    }
                ),
                flush=True,
            )
            if record["elapsed_seconds"] >= args.duration:
                break
            index += 1
            time.sleep(
                max(0, min(index * args.interval, args.duration) - (time.monotonic() - started))
            )
        output.write(
            json.dumps(
                {"event": "end", "timestamp": utc(), "elapsed_seconds": time.monotonic() - started}
            )
            + "\n"
        )
        output.flush()


if __name__ == "__main__":
    main()
