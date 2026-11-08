"""Bounded runtime faults; PostgreSQL fault test requires an explicit disposable URL."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crossx.app import ResearchEngine
from crossx.config import Settings
from crossx.schemas import Level, OrderBook


def book():
    now = datetime.now(UTC)
    return OrderBook(
        exchange="kraken", symbol="BTC/USD",
        bids=(Level(price=Decimal("99"), quantity=Decimal("1")),),
        asks=(Level(price=Decimal("100"), quantity=Decimal("1")),),
        observed_at=now, received_at=now,
    )


class StalledStore:
    async def save_book(self, observation):
        await asyncio.Event().wait()

    async def save_opportunity(self, opportunity):
        await asyncio.Event().wait()


async def test_stalled_storage_fails_within_configured_deadline():
    subject = ResearchEngine(
        Settings(persistence_timeout_seconds=0.02), StalledStore(), {},
    )
    task = asyncio.create_task(subject.observe(book()))
    try:
        # Independent outer watchdog proves the application deadline fired.
        done, _ = await asyncio.wait({task}, timeout=0.2)
        assert task in done, "storage stall leaves consumer hanging indefinitely"
        with pytest.raises(TimeoutError):
            await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("stall", [False, True])
async def test_sigterm_drains_accepted_observations_or_bounds_stall(monkeypatch, caplog, stall):
    import signal
    from unittest.mock import Mock

    from crossx import app

    callbacks = {}
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop, "add_signal_handler", lambda sig, callback: callbacks.update({sig: callback})
    )
    monkeypatch.setattr(loop, "remove_signal_handler", lambda sig: True)
    monkeypatch.setattr(app, "start_http_server", lambda port: (Mock(), Mock()))

    class SlowStore:
        def __init__(self):
            self.entered = asyncio.Event()
            self.saved = []

        async def initialize(self):
            pass

        async def save_book(self, observation):
            self.entered.set()
            await asyncio.sleep(10 if stall else 0.01)
            self.saved.append(observation)

        async def save_opportunity(self, opportunity):
            raise AssertionError("unknown fees must disable opportunities")

        async def close(self):
            pass

    class Feed:
        generation = 0
        reconnect_count = 0
        integrity_error_count = 0
        synchronized_symbols = {"BTC/USD"}

        def __init__(self, emit):
            self.emit = emit

        async def stream(self):
            if self.emit:
                for _ in range(3):
                    yield book()
            await asyncio.Event().wait()

    store = SlowStore()
    monkeypatch.setattr(app, "Database", lambda url: store)
    monkeypatch.setattr(app, "KrakenAdapter", lambda symbols: Feed(True))
    monkeypatch.setattr(app, "CoinbaseAdapter", lambda symbols: Feed(False))
    task = asyncio.create_task(app.run(Settings(shutdown_timeout_seconds=0.02 if stall else 0.2)))
    try:
        await asyncio.wait_for(store.entered.wait(), 0.5)
        callbacks[signal.SIGTERM]()
        await asyncio.wait_for(task, 0.5)
        if stall:
            assert not store.saved
            assert "shutdown_drain_timeout" in caplog.text
        else:
            assert len(store.saved) == 3, "SIGTERM discarded accepted/in-flight observations"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.integration
async def test_postgres_blocked_insert_times_out_and_recovers():
    """Locks only the explicitly opted-in disposable fault database, never live data."""
    import os

    from sqlalchemy import delete, select, text

    from crossx.db import Database, books

    url = os.environ.get("CROSSX_FAULT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set CROSSX_FAULT_TEST_DATABASE_URL to a disposable migrated database")
    database = Database(url)
    observation = book()
    row_ids = []
    try:
        await database.initialize()
        subject = ResearchEngine(
            Settings(persistence_timeout_seconds=0.05), database, {},
        )
        async with database.engine.connect() as blocker:
            transaction = await blocker.begin()
            try:
                await blocker.execute(text("LOCK TABLE book_observations IN ACCESS EXCLUSIVE MODE"))
                async with asyncio.timeout(2):
                    with pytest.raises(TimeoutError):
                        await subject.observe(observation)
            finally:
                await transaction.rollback()
        # A canceled transaction must leave the pool reusable and no partial observation.
        async with database.engine.connect() as connection:
            assert not (
                await connection.execute(
                    select(books.c.id).where(books.c.received_at == observation.received_at)
                )
            ).all()
        await database.save_book(observation)
        async with database.engine.connect() as connection:
            row_ids = list((await connection.execute(
                select(books.c.id).where(books.c.received_at == observation.received_at)
            )).scalars())
        assert len(row_ids) == 1
    finally:
        try:
            if row_ids:
                async with database.engine.begin() as connection:
                    await connection.execute(delete(books).where(books.c.id.in_(row_ids)))
        finally:
            await database.close()


async def test_backpressured_producer_closes_stream_before_returning():
    from crossx.app import produce

    class HeldFeed:
        generation = 0
        synchronized_symbols = {"BTC/USD"}

        def __init__(self):
            self.second = asyncio.Event()
            self.iterator = self.frames()

        def stream(self):
            return self.iterator

        async def frames(self):
            try:
                yield book()
                self.second.set()
                yield book()
            finally:
                self.synchronized_symbols = set()
                self.generation += 1

    feed = HeldFeed()
    queue = asyncio.Queue(maxsize=1)
    task = asyncio.create_task(produce(feed, queue))
    try:
        await asyncio.wait_for(feed.second.wait(), 0.2)
        assert queue.qsize() == 1
        assert not task.done(), "full queue must apply backpressure"
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert not feed.synchronized_symbols, "producer exited without closing its suspended feed"
        assert feed.generation == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await feed.iterator.aclose()
