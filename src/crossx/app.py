"""Public-feed research runner. No exchange account or execution path exists."""

import asyncio
import logging
import signal
import time
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from prometheus_client import start_http_server

from crossx import observability as metrics
from crossx.adapters import build_adapters
from crossx.config import Settings
from crossx.db import Database
from crossx.intelligence import detect_opportunities
from crossx.schemas import Exchange, Opportunity, OrderBook, Symbol

logger = logging.getLogger(__name__)


class Adapter(Protocol):
    generation: int
    synchronized_symbols: set[Symbol]
    reconnect_count: int
    integrity_error_count: int

    def stream(self) -> AsyncGenerator[OrderBook, None]: ...


class Store(Protocol):
    async def save_book(self, book: OrderBook) -> None: ...
    async def save_opportunity(self, opportunity: Opportunity) -> None: ...


@runtime_checkable
class BatchStore(Protocol):
    async def save_books(self, observations: list[OrderBook]) -> None: ...


@dataclass
class QueuedBook:
    book: OrderBook
    generation: int | None = None
    enqueued_at: float = 0


class ResearchEngine:
    def __init__(self, settings: Settings, store: Store, adapters: dict[Exchange, Adapter]) -> None:
        self.settings = settings
        self.store = store
        self.adapters = adapters
        self.latest: dict[tuple[Exchange, Symbol], OrderBook] = {}
        self.latest_generations: dict[tuple[Exchange, Symbol], int] = {}
        self.last_sample: dict[tuple[Exchange, Symbol], float] = {}

    async def _persist(self, observations: list[OrderBook]) -> None:
        if not observations:
            return
        started = time.monotonic()
        metrics.BATCH_SIZE.observe(len(observations))
        try:
            async with asyncio.timeout(self.settings.persistence_timeout_seconds):
                if isinstance(self.store, BatchStore):
                    await self.store.save_books(observations)
                else:
                    for book in observations:
                        await self.store.save_book(book)
        except Exception:
            metrics.PERSIST_FAILURES.labels("books").inc()
            raise
        finally:
            elapsed = time.monotonic() - started
            metrics.TRANSACTION_SECONDS.observe(elapsed)
            for _ in observations:
                # Per-book commit latency, never divided by batch size.
                metrics.PERSIST_SECONDS.observe(elapsed)
        for book in observations:
            metrics.BOOKS.labels(book.exchange, book.symbol).inc()
            metrics.LAST_BOOK.labels(book.exchange, book.symbol).set(book.received_at.timestamp())

    async def observe(
        self,
        book: OrderBook,
        now: datetime | None = None,
        generation: int | None = None,
    ) -> list[Opportunity]:
        return await self.observe_batch([QueuedBook(book, generation)], now)

    async def observe_batch(
        self,
        entries: list[QueuedBook],
        now: datetime | None = None,
    ) -> list[Opportunity]:
        selected = []
        planned = dict(self.last_sample)
        for entry in entries:
            book = entry.book
            key = book.exchange, book.symbol
            tick = time.monotonic()
            if (
                self.settings.book_sample_interval_seconds == 0
                or tick - planned.get(key, float("-inf"))
                >= self.settings.book_sample_interval_seconds
            ):
                selected.append(book)
                planned[key] = tick
            else:
                metrics.SAMPLED_OUT.labels(*key).inc()
        await self._persist(selected)
        self.last_sample = planned
        results = []
        for entry in entries:
            results.extend(await self._evaluate(entry.book, entry.generation, now))
        return results

    async def _evaluate(
        self,
        book: OrderBook,
        generation: int | None,
        now: datetime | None,
    ) -> list[Opportunity]:
        active_generation = self.adapters[book.exchange].generation
        reason = None
        if generation is not None and generation != active_generation:
            reason = "generation"
        else:
            self.latest[(book.exchange, book.symbol)] = book
            self.latest_generations[(book.exchange, book.symbol)] = active_generation
        evaluated_at = now or datetime.now(UTC)
        event_age = (evaluated_at - book.observed_at).total_seconds()
        receive_age = (evaluated_at - book.received_at).total_seconds()
        metrics.EVENT_EVALUATION_AGE.labels(book.exchange, book.symbol).observe(max(0, event_age))
        if reason is None:
            if (
                not book.synchronized
                or book.symbol not in self.adapters[book.exchange].synchronized_symbols
            ):
                reason = "unsynchronized"
            elif event_age < 0 or receive_age < 0:
                reason = "future_timestamp"
            elif event_age > self.settings.max_book_age_seconds:
                reason = "stale_event"
            elif receive_age > self.settings.max_book_age_seconds:
                reason = "stale_receive"
        if reason is not None:
            metrics.REJECTIONS.labels(book.exchange, book.symbol, reason).inc()
            return []
        current = [
            candidate
            for (exchange, symbol), candidate in self.latest.items()
            if symbol == book.symbol
            and symbol in self.adapters[exchange].synchronized_symbols
            and self.latest_generations[(exchange, symbol)] == self.adapters[exchange].generation
        ]
        quantity = (
            self.settings.trade_quantity_btc
            if book.symbol == "BTC/USD"
            else self.settings.trade_quantity_eth
        )
        found = detect_opportunities(
            current,
            self.settings.fees,
            quantity,
            evaluated_at,
            self.settings.max_book_age_seconds,
            self.settings.max_book_skew_seconds,
            self.settings.min_net_profit,
        )
        if found and self.settings.book_sample_interval_seconds > 0:
            # Sampling never removes the book evidence supporting a recorded signal.
            await self._persist(current)
            # The audit write yields to feed tasks. Recheck generations and ages
            # before a sampled-history signal can be committed.
            current = [
                candidate
                for candidate in current
                if self.latest_generations[(candidate.exchange, candidate.symbol)]
                == self.adapters[candidate.exchange].generation
                and candidate.symbol in self.adapters[candidate.exchange].synchronized_symbols
            ]
            found = detect_opportunities(
                current,
                self.settings.fees,
                quantity,
                now or datetime.now(UTC),
                self.settings.max_book_age_seconds,
                self.settings.max_book_skew_seconds,
                self.settings.min_net_profit,
            )
        for opportunity in found:
            try:
                async with asyncio.timeout(self.settings.persistence_timeout_seconds):
                    await self.store.save_opportunity(opportunity)
            except Exception:
                metrics.PERSIST_FAILURES.labels("opportunity").inc()
                raise
            metrics.OPPORTUNITIES.labels(
                opportunity.symbol, opportunity.buy_exchange, opportunity.sell_exchange
            ).inc()
            metrics.NET_PROFIT.observe(float(opportunity.net_profit))
            logger.info(
                "paper_opportunity",
                extra={
                    "symbol": opportunity.symbol,
                    "buy": opportunity.buy_exchange,
                    "sell": opportunity.sell_exchange,
                    "net_profit": str(opportunity.net_profit),
                },
            )
        return found


async def produce(adapter: Adapter, queue: asyncio.Queue[QueuedBook]) -> None:
    async with aclosing(adapter.stream()) as stream:
        async for book in stream:
            metrics.INGRESS.labels(book.exchange, book.symbol).inc()
            metrics.LAST_RECEIVED.labels(book.exchange, book.symbol).set(
                book.received_at.timestamp()
            )
            metrics.LAST_EVENT.labels(book.exchange, book.symbol).set(book.observed_at.timestamp())
            metrics.EVENT_RECEIVE_AGE.labels(book.exchange, book.symbol).observe(
                max(0, (book.received_at - book.observed_at).total_seconds())
            )
            metrics.PRODUCER_DELAY.labels(book.exchange).observe(
                max(0, (datetime.now(UTC) - book.received_at).total_seconds())
            )
            entry = QueuedBook(book, adapter.generation)
            started = time.monotonic()
            await queue.put(entry)
            # No await between admission and timestamp assignment: consumer cannot
            # observe the entry until this event-loop turn yields.
            entry.enqueued_at = time.monotonic()
            metrics.BACKPRESSURE.observe(entry.enqueued_at - started)
            metrics.QUEUE_DEPTH.set(queue.qsize())


async def consume(engine: ResearchEngine, queue: asyncio.Queue[QueuedBook]) -> None:
    while True:
        entries = [await queue.get()]
        for _ in range(engine.settings.persistence_batch_size - 1):
            try:
                entries.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        metrics.QUEUE_DEPTH.set(queue.qsize())
        metrics.IN_FLIGHT.set(len(entries))
        for entry in entries:
            metrics.QUEUE_RESIDENCE.observe(max(0, time.monotonic() - entry.enqueued_at))
        try:
            await engine.observe_batch(entries)
        finally:
            for _ in entries:
                queue.task_done()
            metrics.IN_FLIGHT.set(0)
            metrics.QUEUE_DEPTH.set(queue.qsize())


async def report_adapter_health(adapters: dict[Exchange, Adapter], settings: Settings) -> None:
    while True:
        for exchange, adapter in adapters.items():
            for symbol in settings.symbols:
                metrics.SYNCHRONIZED.labels(exchange, symbol).set(
                    symbol in adapter.synchronized_symbols
                )
            metrics.RECONNECTS.labels(exchange).set(adapter.reconnect_count)
            metrics.INTEGRITY_ERRORS.labels(exchange).set(adapter.integrity_error_count)
        await asyncio.sleep(1)


async def run(settings: Settings) -> None:
    database = Database(settings.database_url)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    server, thread = start_http_server(settings.metrics_port)
    try:
        async with asyncio.timeout(settings.persistence_timeout_seconds):
            await database.initialize()
        adapters: dict[Exchange, Adapter] = dict(
            build_adapters(settings.exchanges, settings.symbols)
        )
        for operation in ("books", "opportunity"):
            metrics.PERSIST_FAILURES.labels(operation).inc(0)
        for exchange in adapters:
            for symbol in settings.symbols:
                metrics.LAST_RECEIVED.labels(exchange, symbol).set(0)
                metrics.LAST_EVENT.labels(exchange, symbol).set(0)
                for reason in (
                    "generation",
                    "unsynchronized",
                    "future_timestamp",
                    "stale_event",
                    "stale_receive",
                ):
                    metrics.REJECTIONS.labels(exchange, symbol, reason).inc(0)
            if exchange not in settings.fees or settings.fees[exchange].taker is None:
                logger.warning("unknown_taker_fee_detection_disabled", extra={"exchange": exchange})
        engine = ResearchEngine(settings, database, adapters)
        queue: asyncio.Queue[QueuedBook] = asyncio.Queue(maxsize=128)
        metrics.READY.set(1)
        logger.info("research_started")
        async with asyncio.TaskGroup() as group:
            producers = [
                group.create_task(produce(adapter, queue)) for adapter in adapters.values()
            ]
            consumer = group.create_task(consume(engine, queue))
            health = group.create_task(report_adapter_health(adapters, settings))
            await stop.wait()
            metrics.READY.set(0)
            # Stop accepting observations, then finish accepted writes within a bounded grace.
            for task in producers:
                task.cancel()
            await asyncio.gather(*producers, return_exceptions=True)
            try:
                async with asyncio.timeout(settings.shutdown_timeout_seconds):
                    await queue.join()
            except TimeoutError:
                metrics.SHUTDOWN_TIMEOUTS.inc()
                logger.warning(
                    "shutdown_drain_timeout",
                    extra={
                        "reason": (
                            f"{queue.qsize()} queued observations "
                            "plus possible in-flight write abandoned"
                        )
                    },
                )
            finally:
                consumer.cancel()
                health.cancel()
    finally:
        metrics.READY.set(0)
        await database.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)


def main() -> None:
    metrics.configure_logging()
    asyncio.run(run(Settings()))


if __name__ == "__main__":
    main()
