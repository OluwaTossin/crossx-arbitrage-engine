"""Bounded persistence benchmark; require a dedicated migrated test database."""

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crossx.db import Database, books
from crossx.schemas import Level, OrderBook


async def run(url, count):
    database = Database(url)
    await database.initialize()
    stamp = datetime.now(UTC)
    observations = [
        OrderBook(
            exchange="kraken",
            symbol="BTC/USD",
            observed_at=stamp + timedelta(microseconds=i),
            received_at=stamp + timedelta(microseconds=i),
            bids=tuple(Level(price=Decimal(60000 - j), quantity=Decimal(".5")) for j in range(25)),
            asks=tuple(Level(price=Decimal(60001 + j), quantity=Decimal(".5")) for j in range(25)),
        )
        for i in range(count)
    ]
    started = time.perf_counter()
    payloads = [
        dict(
            exchange=b.exchange,
            symbol=b.symbol,
            observed_at=b.observed_at,
            received_at=b.received_at,
            payload=b.model_dump(mode="json"),
        )
        for b in observations
    ]
    serialization = time.perf_counter() - started
    results = []
    for batch in (1, 8, 32, 64):
        started = time.perf_counter()
        for index in range(0, count, batch):
            async with database.engine.begin() as connection:
                await connection.execute(books.insert(), payloads[index : index + batch])
        elapsed = time.perf_counter() - started
        results.append(
            {"batch_size": batch, "seconds": elapsed, "rows_per_second": count / elapsed}
        )
    await database.close()
    print(
        json.dumps(
            {
                "timestamp": stamp.isoformat(),
                "rows_per_case": count,
                "serialization_seconds": serialization,
                "results": results,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database-url", required=True)
    p.add_argument("--rows", type=int, default=3000)
    args = p.parse_args()
    asyncio.run(run(args.database_url, args.rows))
