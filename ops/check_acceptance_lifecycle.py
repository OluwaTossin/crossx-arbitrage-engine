"""Bounded stop/start checks restricted to a new acceptance project; preserves volumes."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from ops.observe_acceptance import containers, metrics, monitoring, utc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--original-snapshot", type=Path, required=True)
    parser.add_argument("--app-port", type=int, default=18080)
    parser.add_argument("--prom-port", type=int, default=19091)
    parser.add_argument("--grafana-port", type=int, default=13080)
    args = parser.parse_args()
    if not args.project.startswith("crossx-acceptance-") or args.out.exists():
        parser.error("Require an isolated crossx-acceptance-* project and new evidence path")
    baseline = json.loads(args.original_snapshot.read_text())
    expected_ids = {item["id"] for item in baseline["acceptance"]}
    assert {item["id"] for item in containers(args.project)} == expected_ids
    compose = [
        "docker",
        "compose",
        "--project-name",
        args.project,
        "--file",
        "ops/compose.acceptance.yaml",
    ]
    evidence: dict[str, Any] = {"started_at": utc(), "project": args.project, "steps": []}

    def run(arguments: list[str], timeout: int = 25) -> str:
        result = subprocess.run(
            arguments, capture_output=True, text=True, timeout=timeout, check=True
        )
        return result.stdout.strip()

    def database() -> dict[str, Any]:
        sql = """SELECT json_build_object('rows',(SELECT count(*) FROM book_observations),
          'opportunities',(SELECT count(*) FROM opportunities),
          'pairs',(SELECT json_agg(t) FROM (SELECT exchange,symbol,count(*) AS observations
             FROM book_observations GROUP BY exchange,symbol ORDER BY exchange,symbol) t));"""
        return json.loads(
            run(
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
                    sql,
                ]
            )
        )

    def step(name: str, **details: Any) -> None:
        evidence["steps"].append({"step": name, "timestamp": utc(), **details})
        args.out.write_text(json.dumps(evidence, indent=2) + "\n")
        print(json.dumps({"step": name, "timestamp": utc()}), flush=True)

    def stop() -> list[dict[str, Any]]:
        before = time.monotonic()
        run([*compose, "stop", "-t", "15", "app"])
        state = containers(args.project)
        app = next(item for item in state if item["service"] == "app")
        assert app["state"] == "exited" and app["exit_code"] == 0
        step("graceful_stop", elapsed_seconds=time.monotonic() - before, containers=state)
        return state

    try:
        app = next(item for item in containers(args.project) if item["service"] == "app")
        raw = json.loads(run(["docker", "inspect", app["id"]]))[0]
        fee_env = next(
            value.split("=", 1)[1]
            for value in raw["Config"]["Env"]
            if value.startswith("CROSSX_FEES=")
        )
        fees = json.loads(fee_env)
        assert all(fees[name]["taker"] is None for name in ("kraken", "coinbase"))
        before = database()
        step("before_stop", database=before, metrics=metrics(args.app_port), fees=fees)
        stop()
        stopped = database()
        time.sleep(3)
        stable = database()
        assert stable == stopped
        assert stopped["rows"] >= before["rows"]
        assert stopped["opportunities"] == 0
        step("stopped_database_stable", database=stopped, stable_seconds=3)
        run([*compose, "start", "app"])
        deadline = time.monotonic() + 90
        last_error = None
        while time.monotonic() < deadline:
            try:
                readings = metrics(args.app_port)
                books = [item for item in readings if item["name"] == "crossx_books_total"]
                sync = [item for item in readings if item["name"] == "crossx_book_synchronized"]
                assert len(books) == 4 and all(item["value"] > 0 for item in books)
                assert len(sync) == 4 and all(item["value"] == 1 for item in sync)
                monitoring_state = monitoring(args.prom_port, args.grafana_port)
                targets = monitoring_state["prometheus_targets"]["data"]["activeTargets"]
                assert targets and all(target["health"] == "up" for target in targets)
                break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(1)
        else:
            raise RuntimeError(f"Restart did not become ready within 90 seconds: {last_error}")
        restarted = database()
        step(
            "restart_ready",
            database=restarted,
            metrics=readings,
            monitoring=monitoring_state,
            containers=containers(args.project),
        )
        time.sleep(30)
        resumed = database()
        for item in resumed["pairs"]:
            prior = next(
                p
                for p in restarted["pairs"]
                if p["exchange"] == item["exchange"] and p["symbol"] == item["symbol"]
            )
            assert item["observations"] > prior["observations"]
        assert resumed["opportunities"] == 0
        step("restart_collection_continues", database=resumed, metrics=metrics(args.app_port))
        stop()
        logs = run([*compose, "logs", "--no-color", "app"])
        assert "shutdown_drain_timeout" not in logs
        assert logs.count('"event": "research_started"') == 2
        assert logs.count("unknown_taker_fee_detection_disabled") == 4
        step("logs_verified", logs=logs)
        current_original = containers("crossx-arbitrage-enginepublic")
        assert sorted(current_original, key=lambda x: x["id"]) == sorted(
            baseline["original"], key=lambda x: x["id"]
        )
        step("original_unchanged", original=current_original)
        evidence["passed"] = True
    except Exception as exc:
        evidence["passed"] = False
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        evidence["finished_at"] = utc()
        args.out.write_text(json.dumps(evidence, indent=2) + "\n")


if __name__ == "__main__":
    main()
