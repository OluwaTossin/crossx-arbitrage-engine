"""Complete versioned Decimal simulator snapshots; no external side effects."""

from __future__ import annotations

import hashlib
import heapq
import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Any, cast

from crossx.paper import PaperConfig, Position, Simulator, Trade, Wallet
from crossx.schemas import Exchange, OrderBook, Symbol

SCALARS = ("initial_nav", "peak_nav", "drawdown", "realized_pnl", "paired_pnl", "fees_paid")


def canonical(value: object) -> bytes:
    def encode(item: object) -> str:
        if isinstance(item, Decimal):
            return str(item)
        if isinstance(item, datetime):
            return item.isoformat()
        raise TypeError(type(item).__name__)

    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=encode, allow_nan=False
    ).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def snapshot(sim: Simulator) -> dict[str, Any]:
    data = {
        "format": 1,
        "config": sim.config.model_dump(mode="json"),
        "now": sim.now.isoformat() if sim.now else None,
        "initialized": sim.initialized,
        "sequence": sim.sequence,
        "event_sequence": sim.event_sequence,
        "latest": [b.model_dump(mode="json") for b in sim.latest.values()],
        "wallets": {v: asdict(w) for v, w in sim.wallets.items()},
        "pending": [
            {"due": due, "number": number, "side": side, "trade": asdict(t)}
            for due, number, side, t in sorted(sim.pending)
        ],
        "consumed": [
            {
                "venue": v,
                "symbol": s,
                "side": side,
                "levels": [[p, q] for p, q in sorted(levels.items())],
            }
            for (v, s, side), levels in sorted(sim.consumed.items())
        ],
        "unhedged": sim.unhedged,
        "counts": sim.counts,
        "rejections": sim.rejections,
        **{name: str(getattr(sim, name)) for name in SCALARS},
    }
    return cast(dict[str, Any], json.loads(canonical(data)))


def restore(state: dict[str, Any], emit: Callable[[dict[str, object]], None]) -> Simulator:
    if state.get("format") != 1:
        raise ValueError("Unsupported recovery snapshot version")
    sim = Simulator(PaperConfig.model_validate(state["config"]), emit)
    sim.now = datetime.fromisoformat(state["now"]) if state["now"] else None
    sim.initialized = state["initialized"]
    sim.sequence = int(state["sequence"])
    sim.event_sequence = int(state["event_sequence"])
    for name in SCALARS:
        setattr(sim, name, Decimal(state[name]))
    sim.latest = {}
    for raw in state["latest"]:
        book = OrderBook.model_validate(raw)
        sim.latest[(book.exchange, book.symbol)] = book
    sim.wallets = {}
    for v in sim.config.venues:
        w = state["wallets"][v]
        wallet = Wallet(
            initial_nav=Decimal(w["initial_nav"]),
            realized_pnl=Decimal(w["realized_pnl"]),
            fees_paid=Decimal(w["fees_paid"]),
            cash=Decimal(w["cash"]),
            reserved_cash=Decimal(w["reserved_cash"]),
        )
        wallet.positions = {
            a: Position(Decimal(p["quantity"]), Decimal(p["cost"]))
            for a, p in w["positions"].items()
        }
        wallet.reserved = {a: Decimal(q) for a, q in w["reserved"].items()}
        if wallet.cash < 0 or wallet.reserved_cash < 0 or wallet.reserved_cash > wallet.cash:
            raise ValueError("Invalid recovered cash reservation")
        if any(
            p.quantity < 0 or p.cost < 0 or wallet.reserved.get(a, Decimal(0)) > p.quantity
            for a, p in wallet.positions.items()
        ):
            raise ValueError("Invalid recovered inventory")
        sim.wallets[v] = wallet
    sim.pending = []
    for p in state["pending"]:
        t = dict(p["trade"])
        for name in (
            "requested",
            "cash_reserved",
            "buy_quantity",
            "buy_cost",
            "sell_quantity",
            "sell_proceeds",
        ):
            t[name] = Decimal(t[name])
        t["created"] = datetime.fromisoformat(t["created"])
        trade = Trade(**t)
        heapq.heappush(
            sim.pending, (datetime.fromisoformat(p["due"]), int(p["number"]), p["side"], trade)
        )
    sim.consumed = {}
    for c in state["consumed"]:
        key = cast(Exchange, c["venue"]), cast(Symbol, c["symbol"]), c["side"]
        sim.consumed[key] = {Decimal(p): Decimal(q) for p, q in c["levels"]}
    sim.unhedged = {a: Decimal(q) for a, q in state["unhedged"].items()}
    sim.counts = dict(state["counts"])
    sim.rejections = dict(state["rejections"])
    if snapshot(sim) != state:
        raise ValueError("Snapshot did not round-trip exactly")
    return sim


async def inspect_session(session: str) -> dict[str, object]:
    from sqlalchemy import select

    from crossx.config import Settings
    from crossx.db import Database, paper_attempts
    from crossx.forward_store import ForwardStore, RecoveryConflict

    database = Database(Settings().database_url)
    try:
        try:
            loaded = await ForwardStore(database).load(session)
            sim = restore(loaded["state"]["simulator"], lambda e: None)
        except RecoveryConflict as exc:
            return {"session_id": session, "classification": "irrecoverable", "reason": str(exc)}
        async with database.engine.connect() as c:
            attempts = (
                (
                    await c.execute(
                        select(paper_attempts)
                        .where(paper_attempts.c.session_id == session)
                        .order_by(paper_attempts.c.started_at)
                    )
                )
                .mappings()
                .all()
            )
        return {
            "session_id": session,
            "classification": "checkpoint_recoverable",
            "status": loaded["status"],
            "checkpoint_version": loaded["version"],
            "source_cursor": loaded["state"]["cursor"],
            "pending_legs": len(sim.pending),
            "attempts": [dict(a) for a in attempts],
            "warning": "An active attempt without ended_at has an unknown interruption boundary",
        }
    finally:
        await database.close()


def main() -> None:
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Inspect a durable paper recovery checkpoint")
    parser.add_argument("--inspect", required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(inspect_session(args.inspect)), indent=2, default=str))


if __name__ == "__main__":
    main()
