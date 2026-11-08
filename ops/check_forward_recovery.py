"""Actual SIGKILL/restart checks with synthetic spot books in an isolated test DB."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from crossx.db import Database, source_windows
from crossx.forward import process_window
from crossx.forward_store import ForwardStore
from crossx.paper import VENUES, MarketRules, PaperConfig, Simulator
from crossx.recovery import snapshot
from crossx.schemas import FeeSchedule, Level, OrderBook


async def run(args: argparse.Namespace) -> None:
    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=False)
    db = Database(args.database_url)
    store = ForwardStore(db)
    env = {**os.environ, "CROSSX_DATABASE_URL": args.database_url}
    results = []
    try:
        for phase in ("reservation", "buy", "settlement"):
            config = PaperConfig(
                gbp_usd="1.25",
                assumption_at=datetime.now(UTC),
                evaluation_log_mode="aggregate",
                fx_cost_rate=0,
                funding_fee_rate=0,
                slippage_rate=0,
                rebalancing_cost_usd=0,
                min_profit_usd=0,
                min_return=0,
                max_exchange_fraction=".9",
                max_inventory_fraction=".99",
                max_trade_usd=1000,
                trade_quantity_btc=".1",
                latency_ms=2000,
                second_leg_latency_ms=1000,
                max_age_seconds=30,
                fees={v: FeeSchedule(taker=".001") for v in VENUES},
                rules={
                    v + ":" + s: MarketRules(
                        quantity_step=".001", min_quantity=".001", min_notional=1, max_quantity=10
                    )
                    for v in VENUES
                    for s in ("BTC/USD", "ETH/USD")
                },
            )
            directory = root / phase
            directory.mkdir()
            cfg = directory / "synthetic-config.json"
            cfg.write_text(config.model_dump_json(indent=2) + "\n")

            def command(out: Path, resume: str | None = None, cfg: Path = cfg) -> list[str]:
                c = [
                    sys.executable,
                    "-m",
                    "crossx.forward",
                    "--config",
                    str(cfg),
                    "--duration",
                    "8" if resume else "60",
                    "--source-commit",
                    args.source_commit,
                    "--out",
                    str(out),
                ]
                return c + (["--resume", resume] if resume else [])

            child = await asyncio.create_subprocess_exec(
                *command(directory / "first"),
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            session_file = directory / "first/session.json"
            for _ in range(200):
                if session_file.exists():
                    break
                await asyncio.sleep(0.02)
            else:
                raise RuntimeError("worker did not start")
            session = json.loads(session_file.read_text())["session_id"]
            stamp = datetime.now(UTC)

            def book(
                v: str, s: str, bid: str = "99", ask: str = "100", stamp: datetime = stamp
            ) -> OrderBook:
                return OrderBook(
                    exchange=v,
                    symbol=s,
                    bids=(Level(price=bid, quantity=10),),
                    asks=(Level(price=ask, quantity=10),),
                    observed_at=stamp,
                    received_at=stamp,
                )

            inputs = [book(v, s) for v in VENUES for s in ("BTC/USD", "ETH/USD")]
            inputs.append(book("coinbase", "BTC/USD", "110", "111"))
            await db.save_books(inputs)
            cut = None
            for _ in range(800):
                loaded = await store.load(session)
                state = loaded["state"]["simulator"]
                pending = state["pending"]
                counts = state["counts"]
                target = (
                    phase == "reservation"
                    and pending
                    and pending[0]["side"] == "buy"
                    or phase == "buy"
                    and pending
                    and pending[0]["side"] == "sell"
                    or phase == "settlement"
                    and counts.get("trade_completed", 0) == 1
                )
                if target:
                    cut = loaded
                    break
                if child.returncode is not None:
                    raise RuntimeError("worker failed early")
                await asyncio.sleep(0.02)
            if cut is None:
                child.kill()
                await child.wait()
                raise RuntimeError("fault boundary not observed")
            killed_at = datetime.now(UTC).isoformat()
            child.send_signal(signal.SIGKILL)
            await child.communicate()
            resumed = await asyncio.create_subprocess_exec(
                *command(directory / "resumed", session),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(resumed.communicate(), 30)
            if resumed.returncode:
                raise RuntimeError(stderr.decode())
            final = await store.load(session)
            replay = Simulator(config, lambda e: None)
            async with db.engine.connect() as c:
                versions = (
                    (
                        await c.execute(
                            select(source_windows.c.version)
                            .where(source_windows.c.session_id == session)
                            .order_by(source_windows.c.version)
                        )
                    )
                    .scalars()
                    .all()
                )
            for version in versions:
                process_window(replay, await store.read_window(session, int(version)))
            assert snapshot(replay) == final["state"]["simulator"]
            assert final["state"]["simulator"]["counts"].get("trade_completed") == 1
            result = {
                "phase": phase,
                "session_id": session,
                "killed_at": killed_at,
                "child_returncode": child.returncode,
                "cut_version": cut["version"],
                "final_version": final["version"],
                "immutable_replay_state_equal": True,
                "completed_trades": 1,
                "report": json.loads((directory / "resumed/report.json").read_text()),
            }
            results.append(result)
        (root / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    finally:
        await db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database-url", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--source-commit", required=True)
    asyncio.run(run(p.parse_args()))
