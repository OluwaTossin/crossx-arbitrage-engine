"""Deterministic burst, failure, sampling and freshness regressions."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from prometheus_client import REGISTRY

from crossx.app import QueuedBook, ResearchEngine, consume, produce
from crossx.config import Settings
from crossx.schemas import FeeSchedule, Level, OrderBook


class Feed:
    generation = 1
    synchronized_symbols = {"BTC/USD", "ETH/USD"}
    reconnect_count = 0
    integrity_error_count = 0


def book(exchange="kraken", age=0):
    now = datetime.now(UTC)
    bid, ask = ("99", "100") if exchange == "kraken" else ("110", "111")
    return OrderBook(
        exchange=exchange,
        symbol="BTC/USD",
        observed_at=now - timedelta(seconds=age),
        received_at=now,
        bids=(Level(price=bid, quantity="2"),),
        asks=(Level(price=ask, quantity="2"),),
    )


class BatchStore:
    def __init__(self):
        self.batches = []
        self.signals = []
        self.fail = False

    async def save_books(self, books):
        await asyncio.sleep(0.001)
        if self.fail:
            raise RuntimeError("injected database failure")
        self.batches.append(list(books))

    async def save_book(self, observation):
        raise AssertionError("batch-capable storage must use one batch transaction")

    async def save_opportunity(self, opportunity):
        self.signals.append(opportunity)


def subject(**overrides):
    settings = Settings(
        trade_quantity_btc=Decimal("1"),
        fees={v: FeeSchedule(taker=".001") for v in ("kraken", "coinbase")},
        **overrides,
    )
    return ResearchEngine(settings, BatchStore(), {v: Feed() for v in ("kraken", "coinbase")})


async def test_fifo_burst_batches_without_raising_capacity_or_losing_books():
    engine = subject()
    count = 1024
    emitted = []

    class Burst(Feed):
        async def stream(self):
            try:
                for _ in range(count):
                    item = book()
                    emitted.append(item)
                    yield item
            finally:
                self.generation += 1
                self.synchronized_symbols = set()

    queue = asyncio.Queue(maxsize=128)
    worker = asyncio.create_task(consume(engine, queue))
    producer = asyncio.create_task(produce(Burst(), queue))
    try:
        async with asyncio.timeout(2):
            await producer
            await queue.join()
        persisted = [b for batch in engine.store.batches for b in batch]
        assert persisted == emitted
        assert len(persisted) == count
        assert all(1 <= len(batch) <= 32 for batch in engine.store.batches)
        assert len(engine.store.batches) < 40
        assert queue.maxsize == 128 and queue.empty()
    finally:
        worker.cancel()
        producer.cancel()
        await asyncio.gather(worker, producer, return_exceptions=True)


async def test_generation_reset_during_batch_commit_cannot_record_signal():
    engine = subject()
    await engine.observe(book("coinbase"), generation=1)
    original = engine.store.save_books

    async def reset_during_commit(items):
        await original(items)
        engine.adapters["kraken"].generation += 1

    engine.store.save_books = reset_during_commit
    assert await engine.observe_batch([QueuedBook(book(), 1)]) == []
    assert not engine.store.signals


async def test_stale_event_exclusion_and_metrics_preserve_original_timestamp():
    engine = subject()
    await engine.observe(book("coinbase"), generation=1)
    stale = book(age=6)
    labels = {"exchange": "kraken", "symbol": "BTC/USD", "reason": "stale_event"}
    before = REGISTRY.get_sample_value("crossx_detection_rejections_total", labels) or 0
    assert await engine.observe(stale, generation=1) == []
    assert not engine.store.signals
    assert engine.store.batches[-1][0].observed_at == stale.observed_at
    assert REGISTRY.get_sample_value("crossx_detection_rejections_total", labels) == before + 1


async def test_failed_batch_does_not_update_cache_or_record_signals():
    engine = subject()
    engine.store.fail = True
    with pytest.raises(RuntimeError, match="injected"):
        await engine.observe_batch([QueuedBook(book(), 1), QueuedBook(book("coinbase"), 1)])
    assert not engine.latest and not engine.store.signals


async def test_sampling_is_opt_in_and_keeps_signal_supporting_books():
    engine = subject(book_sample_interval_seconds=60)
    first, second = book(), book()
    await engine.observe(first, generation=1)
    await engine.observe(second, generation=1)
    assert [b for batch in engine.store.batches for b in batch] == [first]
    (signal,) = await engine.observe(book("coinbase"), generation=1)
    assert signal.net_profit > 0
    assert second in [b for batch in engine.store.batches for b in batch]
    assert engine.store.signals == [signal]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"persistence_batch_size": 0},
        {"persistence_batch_size": 129},
        {"book_sample_interval_seconds": -1},
        {"retention_days": 0},
    ],
)
def test_invalid_hardening_settings_rejected(kwargs):
    with pytest.raises(ValueError):
        Settings(**kwargs)


async def test_sampling_support_commit_rechecks_generation_before_signal():
    engine = subject(book_sample_interval_seconds=60)
    await engine.observe(book(), generation=1)
    original = engine.store.save_books

    async def reset_support_batch(items):
        await original(items)
        if len(items) == 2:
            engine.adapters["kraken"].generation += 1

    engine.store.save_books = reset_support_batch
    assert await engine.observe(book("coinbase"), generation=1) == []
    assert not engine.store.signals
