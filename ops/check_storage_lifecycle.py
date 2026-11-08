"""Bounded lifecycle probes, exclusively against a named disposable acceptance project."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

from ops.observe_acceptance import containers


def command(args: list[str]) -> str:
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=45)
    except subprocess.CalledProcessError as exc:
        print(exc.output, flush=True)
        raise


async def run(project: str, port: int, output: Path) -> None:
    if not project.startswith("crossx-acceptance-"):
        raise ValueError("Require an isolated acceptance project")
    output.mkdir(parents=True, exist_ok=False)
    compose = ["docker", "compose", "-p", project, "-f", "ops/compose.acceptance.yaml"]
    results: list[dict[str, object]] = []

    def action(*args: str) -> None:
        if args[0] == "start":
            # Reuse existing containers, including intentional unhealthy-DB probes.
            # Compose's dependency-health gate is unrelated to process restart.
            ids = [c["id"] for c in containers(project) if c["service"] in args[1:]]
            command(["docker", "start", *ids])
        else:
            command([*compose, *args])

    def metric_text() -> str:
        return command(
            [
                *compose,
                "exec",
                "-T",
                "storage-metrics",
                "python",
                "-c",
                "import urllib.request;print(urllib.request.urlopen("
                "'http://127.0.0.1:8001/metrics',timeout=2).read().decode())",
            ]
        )

    async def healthy(value: int) -> str:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            try:
                text = metric_text()
                if f"crossx_storage_up {value}.0" in text:
                    return text
            except subprocess.SubprocessError:
                pass
            await asyncio.sleep(0.25)
        raise AssertionError(f"Telemetry did not reach up={value}")

    def stopped(label: str, *services: str) -> None:
        started = time.monotonic()
        at = datetime.now(UTC).isoformat()
        action("stop", *services)
        elapsed = time.monotonic() - started
        state = [c for c in containers(project) if not services or c["service"] in services]
        results.append({"case": label, "started_at": at, "stop_seconds": elapsed, "state": state})
        (output / "results.json").write_text(json.dumps(results, indent=2))
        assert all(c["state"] == "exited" and c["exit_code"] == 0 for c in state), state
        assert elapsed < 15, elapsed

    async def connect() -> asyncpg.Connection:
        for _ in range(80):
            try:
                return await asyncpg.connect(
                    host="127.0.0.1",
                    port=port,
                    user="crossx",
                    password="crossx",
                    database="crossx",
                    timeout=1,
                )
            except (OSError, asyncpg.PostgresError):
                await asyncio.sleep(0.25)
        raise AssertionError("Disposable database unavailable")

    async def fingerprint() -> dict[str, object]:
        db = await connect()
        try:
            evidence = {}
            for table in (
                "book_observations",
                "opportunities",
                "paper_sessions",
                "paper_events",
                "paper_source_windows",
                "paper_attempts",
            ):
                # Fixed internal table names; no user-supplied SQL identifiers.
                rows = await db.fetch(f"SELECT to_jsonb(t)::text AS value FROM {table} t")
                data = "\n".join(sorted(r["value"] for r in rows)).encode()
                evidence[table] = {"rows": len(rows), "sha256": hashlib.sha256(data).hexdigest()}
            return evidence
        finally:
            await db.close()

    action("start", "storage-metrics")
    (output / "normal-metrics.txt").write_text(await healthy(1))
    # Stop only the new collector to freeze the research dataset for exporter-only checks.
    stopped("collector_freeze", "app")
    before = await fingerprint()
    stopped("normal_sigterm", "storage-metrics")
    action("start", "storage-metrics")
    (output / "restart-metrics.txt").write_text(await healthy(1))
    stopped("postgres_unavailable", "postgres")
    (output / "unavailable-metrics.txt").write_text(await healthy(0))
    stopped("sigterm_database_unavailable", "storage-metrics")
    action("start", "postgres")
    db = await connect()
    await db.close()
    action("start", "storage-metrics")
    (output / "recovered-metrics.txt").write_text(await healthy(1))
    stopped("before_blocked_poll", "storage-metrics")
    # Suspend only this disposable PostgreSQL process: requests genuinely cannot complete.
    postgres = next(c["id"] for c in containers(project) if c["service"] == "postgres")
    command(["docker", "pause", postgres])
    try:
        sidecar = next(c["id"] for c in containers(project) if c["service"] == "storage-metrics")
        command(["docker", "start", sidecar])
        # HTTP starts before the DB poll; observe its initial unsuccessful state.
        await healthy(0)
        await asyncio.sleep(0.5)
        stopped("sigterm_during_paused_database_read", "storage-metrics")
    finally:
        command(["docker", "unpause", postgres])
    db = await connect()
    try:
        released_at = time.monotonic()
        while True:
            connections = await db.fetchval(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE usename='crossx' AND backend_type='client backend' "
                "AND pid<>pg_backend_pid()"
            )
            if connections == 0:
                break
            assert time.monotonic() - released_at < 5, connections
            await asyncio.sleep(0.1)
        results.append(
            {
                "case": "paused_database_connections_released",
                "remaining": connections,
                "after_resume_seconds": time.monotonic() - released_at,
            }
        )
    finally:
        await db.close()
    action("start", "storage-metrics")
    await healthy(1)
    after = await fingerprint()
    (output / "data-preservation.json").write_text(
        json.dumps({"before": before, "after": after, "identical": before == after}, indent=2)
    )
    assert before == after
    action("start", "app")
    await asyncio.sleep(3)
    stopped("all_services_compose_stop")
    (output / "results.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project", required=True)
    p.add_argument("--database-port", type=int, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    os.environ.update(ACCEPTANCE_DB_PORT=str(args.database_port))
    asyncio.run(run(args.project, args.database_port, args.out))
