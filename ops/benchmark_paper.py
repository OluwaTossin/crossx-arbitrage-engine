"""Controlled synthetic benchmark; never compare this with public market rates."""

from __future__ import annotations

import json
import statistics
import time
import tracemalloc
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crossx.intelligence import detect_opportunities
from crossx.paper import VENUES, MarketRules, PaperConfig, Simulator
from crossx.schemas import FeeSchedule, Level, OrderBook, Symbol


def main() -> None:
    symbols: tuple[Symbol, ...] = ("BTC/USD", "ETH/USD")
    t = datetime(2026, 1, 1, tzinfo=UTC)
    frames = []
    fees = {v: FeeSchedule(taker="0.001") for v in VENUES}
    config = PaperConfig(
        gbp_usd="1.25",
        assumption_at=t,
        fees=fees,
        min_profit_usd=0,
        min_return=0,
        rules={
            v + ":" + s: MarketRules(
                quantity_step=".0001", min_quantity=".0001", min_notional="1", max_quantity="10"
            )
            for v in VENUES
            for s in symbols
        },
    )
    for i in range(8192):
        venue = VENUES[i % 4]
        symbol = symbols[(i // 4) % 2]
        price = Decimal(10000 + (i % 4) * 100)
        stamp = t + timedelta(milliseconds=i * 10)
        frames.append(
            OrderBook(
                exchange=venue,
                symbol=symbol,
                bids=tuple(Level(price=price - j, quantity=".1") for j in range(25)),
                asks=tuple(Level(price=price + 2 + j, quantity=".1") for j in range(25)),
                observed_at=stamp,
                received_at=stamp,
            )
        )
    results = {}
    for mode in ("detector_only", "capital_paper"):
        latest = {}
        sim = Simulator(config, lambda e: None)
        timings = []
        tracemalloc.start()
        started = time.perf_counter()
        for book in frames:
            tick = time.perf_counter()
            if mode == "detector_only":
                latest[(book.exchange, book.symbol)] = book
                detect_opportunities(
                    [b for b in latest.values() if b.symbol == book.symbol],
                    fees,
                    Decimal(".001"),
                    book.received_at,
                )
            else:
                sim.ingest(book)
            timings.append(time.perf_counter() - tick)
        seconds = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        results[mode] = {
            "observations": len(frames),
            "seconds": seconds,
            "rows_per_second": len(frames) / seconds,
            "mean_processing_ms": statistics.mean(timings) * 1000,
            "p95_processing_ms": sorted(timings)[int(len(timings) * 0.95)] * 1000,
            "traced_peak_bytes": peak,
        }
        if mode == "capital_paper":
            results[mode]["result"] = sim.finish()
    print(
        json.dumps(
            {
                "workload": (
                    "same 8192 deterministic depth-25 frames; different computations; no network/DB"
                ),
                "results": results,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
