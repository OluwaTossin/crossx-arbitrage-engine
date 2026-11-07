"""Independent bounded replay against disposable PG; compares the reviewed consumer.

4096 synthetic frames are available at t=0. Delayed reads model a buffered source,
not a claim about exchange/network causality. Writes are appended, never deleted.
"""

import argparse
import asyncio
import json
import subprocess
import sys
import time
import types
from datetime import UTC, datetime
from decimal import Decimal
from statistics import mean

from sqlalchemy import func, select

from crossx import app
from crossx.config import Settings
from crossx.db import Database, books
from crossx.schemas import Level, OrderBook


class TracedQueue(asyncio.Queue):
    def __init__(self):
        super().__init__(maxsize=128)
        self.admitted = {}
        self.waits = []
        self.residence = []
        self.peak = 0

    async def put(self, item):
        started = time.monotonic()
        await super().put(item)
        b = item[0] if isinstance(item, tuple) else item.book
        self.admitted[id(b)] = time.monotonic()
        self.waits.append(time.monotonic() - started)
        self.peak = max(self.peak, self.qsize())

    def get_nowait(self):
        item = super().get_nowait()
        b = item[0] if isinstance(item, tuple) else item.book
        self.residence.append(time.monotonic() - self.admitted.pop(id(b)))
        return item


class TimedDatabase(Database):
    def __init__(self, url):
        super().__init__(url)
        self.transaction_seconds = []
        self.commit_seconds = []

    async def save_book(self, b):
        started = time.monotonic()
        await super().save_book(b)
        elapsed = time.monotonic() - started
        self.transaction_seconds.append(elapsed)
        self.commit_seconds.append(elapsed)

    async def save_books(self, items):
        started = time.monotonic()
        await super().save_books(items)
        elapsed = time.monotonic() - started
        self.transaction_seconds.append(elapsed)
        self.commit_seconds.extend([elapsed] * len(items))


def distribution(items):
    items = sorted(items)
    return {"mean": mean(items), "p95": items[int((len(items) - 1) * 0.95)], "max": max(items)}


async def phase(module, url, count):
    database = TimedDatabase(url)
    await database.initialize()
    stamp = datetime.now(UTC)
    prototype = OrderBook(
        exchange="kraken",
        symbol="BTC/USD",
        observed_at=stamp,
        received_at=stamp,
        bids=tuple(Level(price=Decimal(60000 - j), quantity=Decimal(".5")) for j in range(25)),
        asks=tuple(Level(price=Decimal(60001 + j), quantity=Decimal(".5")) for j in range(25)),
    )

    class Feed:
        generation = 1
        synchronized_symbols = {"BTC/USD"}
        reconnect_count = 0
        integrity_error_count = 0
        ages = []

        async def stream(self):
            try:
                for _ in range(count):
                    received = datetime.now(UTC)
                    self.ages.append((received - stamp).total_seconds())
                    yield prototype.model_copy(update={"received_at": received})
            finally:
                self.generation += 1
                self.synchronized_symbols = set()

    feed = Feed()
    engine = module.ResearchEngine(Settings(), database, {"kraken": feed})
    queue = TracedQueue()
    started = time.monotonic()
    consumer = asyncio.create_task(module.consume(engine, queue))
    producer = asyncio.create_task(module.produce(feed, queue))
    try:
        async with asyncio.timeout(30):
            await producer
            await queue.join()
        elapsed = time.monotonic() - started
        async with database.engine.connect() as connection:
            stored = await connection.scalar(
                select(func.count()).select_from(books).where(books.c.observed_at == stamp)
            )
        assert stored == count and len(queue.residence) == count
        return {
            "timestamp": stamp.isoformat(),
            "seconds": elapsed,
            "rows": stored,
            "rows_per_second": count / elapsed,
            "queue_capacity": 128,
            "queue_peak": queue.peak,
            "admission_wait_seconds": distribution(queue.waits),
            "residence_seconds": distribution(queue.residence),
            "commit_latency_per_book_seconds": distribution(database.commit_seconds),
            "transactions": len(database.transaction_seconds),
            "event_age_at_receive_seconds": distribution(feed.ages),
            "event_age_over_5_seconds": sum(a > 5 for a in feed.ages),
        }
    finally:
        consumer.cancel()
        producer.cancel()
        await asyncio.gather(consumer, producer, return_exceptions=True)
        await database.close()


async def main(args):
    source = subprocess.check_output(
        ["git", "show", f"{args.base_ref}:src/crossx/app.py"], text=True
    )
    baseline = types.ModuleType("crossx._baseline_app")
    sys.modules[baseline.__name__] = baseline
    exec(compile(source, "baseline_app.py", "exec"), baseline.__dict__)
    before = await phase(baseline, args.database_url, args.rows)
    after = await phase(app, args.database_url, args.rows)
    print(
        json.dumps(
            {
                "base_ref": args.base_ref,
                "source_model": "all synthetic frames available at t=0",
                "before": before,
                "after": after,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--rows", type=int, default=4096)
    parser.add_argument("--base-ref", default="origin/codex/phase-1-operational-acceptance")
    arguments = parser.parse_args()
    if not 128 <= arguments.rows <= 10000:
        parser.error("Require 128..10000 rows")
    asyncio.run(main(arguments))
