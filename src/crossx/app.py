"""Public-feed research runner. No exchange account or execution path exists."""

import asyncio
import logging
import signal
from collections.abc import AsyncGenerator
from contextlib import aclosing
from datetime import UTC, datetime
from typing import Protocol

from prometheus_client import start_http_server

from crossx import observability as metrics
from crossx.adapters.coinbase import CoinbaseAdapter
from crossx.adapters.kraken import KrakenAdapter
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


class ResearchEngine:
    def __init__(self, settings: Settings, store: Store, adapters: dict[Exchange, Adapter]) -> None:
        self.settings = settings
        self.store = store
        self.adapters = adapters
        self.latest: dict[tuple[Exchange, Symbol], OrderBook] = {}
        self.latest_generations: dict[tuple[Exchange, Symbol], int] = {}

    async def observe(
        self, book: OrderBook, now: datetime | None = None, generation: int | None = None,
    ) -> list[Opportunity]:
        # Database failure stops processing instead of losing observations silently.
        async with asyncio.timeout(self.settings.persistence_timeout_seconds):
            with metrics.PERSIST_SECONDS.time():
                await self.store.save_book(book)
        metrics.BOOKS.labels(book.exchange, book.symbol).inc()
        metrics.LAST_BOOK.labels(book.exchange, book.symbol).set(book.received_at.timestamp())
        active_generation = self.adapters[book.exchange].generation
        if generation is not None and generation != active_generation:
            return []
        self.latest[(book.exchange, book.symbol)] = book
        self.latest_generations[(book.exchange, book.symbol)] = active_generation
        current = [
            candidate for (exchange, symbol), candidate in self.latest.items()
            if symbol == book.symbol and symbol in self.adapters[exchange].synchronized_symbols
            and self.latest_generations[(exchange, symbol)] == self.adapters[exchange].generation
        ]
        quantity = (
            self.settings.trade_quantity_btc if book.symbol == "BTC/USD"
            else self.settings.trade_quantity_eth
        )
        found = detect_opportunities(
            current, self.settings.fees, quantity, now or datetime.now(UTC),
            self.settings.max_book_age_seconds, self.settings.max_book_skew_seconds,
            self.settings.min_net_profit,
        )
        for opportunity in found:
            async with asyncio.timeout(self.settings.persistence_timeout_seconds):
                await self.store.save_opportunity(opportunity)
            metrics.OPPORTUNITIES.labels(
                opportunity.symbol, opportunity.buy_exchange, opportunity.sell_exchange
            ).inc()
            metrics.NET_PROFIT.observe(float(opportunity.net_profit))
            logger.info("paper_opportunity", extra={
                "symbol": opportunity.symbol, "buy": opportunity.buy_exchange,
                "sell": opportunity.sell_exchange, "net_profit": str(opportunity.net_profit),
            })
        return found


async def produce(adapter: Adapter, queue: asyncio.Queue[tuple[OrderBook, int]]) -> None:
    # Cancellation can arrive at queue.put while the generator is suspended at yield.
    # Close explicitly so connection state is invalidated before the producer returns.
    async with aclosing(adapter.stream()) as stream:
        async for book in stream:
            # Bounded memory; delayed observations fail freshness checks.
            await queue.put((book, adapter.generation))
            metrics.QUEUE_DEPTH.set(queue.qsize())


async def consume(
    engine: ResearchEngine, queue: asyncio.Queue[tuple[OrderBook, int]],
) -> None:
    while True:
        book, generation = await queue.get()
        try:
            await engine.observe(book, generation=generation)
        finally:
            queue.task_done()
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
        adapters: dict[Exchange, Adapter] = {
            "kraken": KrakenAdapter(settings.symbols),
            "coinbase": CoinbaseAdapter(settings.symbols),
        }
        for exchange in adapters:
            if exchange not in settings.fees or settings.fees[exchange].taker is None:
                logger.warning("unknown_taker_fee_detection_disabled", extra={"exchange": exchange})
        engine = ResearchEngine(settings, database, adapters)
        queue: asyncio.Queue[tuple[OrderBook, int]] = asyncio.Queue(maxsize=128)
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
                logger.warning("shutdown_drain_timeout", extra={"reason":
                    f"{queue.qsize()} queued observations plus possible in-flight write abandoned"})
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
