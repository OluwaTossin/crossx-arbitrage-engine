"""Shared, strictly read-only market-data transport and validation."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import websockets

from crossx.schemas import Level, OrderBook, Symbol

logger = logging.getLogger(__name__)


class IntegrityError(ValueError):
    """Local book cannot be proved synchronized; require a new connection snapshot."""


def decimal(value: Any) -> Decimal:
    # JSON floats have already lost the precision needed for Kraken CRC32.
    if isinstance(value, float):
        raise IntegrityError("Float received; decode JSON with parse_float=Decimal")
    result = Decimal(value)
    if not result.is_finite():
        raise IntegrityError("Nonfinite book value")
    return result


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise IntegrityError("Naive exchange timestamp")
    return result.astimezone(UTC)


class PublicAdapter:
    url: str
    exchange: str
    observation_depth = 25

    def __init__(self, symbols: tuple[Symbol, ...]) -> None:
        if not symbols or any(s not in ("BTC/USD", "ETH/USD") for s in symbols):
            raise ValueError("Only BTC/USD and ETH/USD spot markets are supported")
        self.symbols = symbols
        self.generation = 0
        self.last_error: str | None = None
        self.reconnect_count = 0
        self.integrity_error_count = 0
        self.reset()

    def reset(self) -> None:
        self.generation += 1
        self.synchronized_symbols: set[Symbol] = set()
        self.books: dict[str, dict[str, dict[Decimal, Decimal]]] = {}
        self.last_events: dict[str, datetime] = {}
        self.sequences: dict[str, int] = {}

    def subscriptions(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def process(self, message: dict[str, Any], received_at: datetime) -> list[OrderBook]:
        try:
            return self._process(message, received_at)
        except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
            self.integrity_error_count += 1
            self.reset()
            raise IntegrityError(str(exc)) from exc

    def _process(self, message: dict[str, Any], received_at: datetime) -> list[OrderBook]:
        raise NotImplementedError

    def update(self, symbol: str, side: str, price: Any, quantity: Any) -> None:
        p, q = decimal(price), decimal(quantity)
        if p <= 0 or q < 0:
            raise IntegrityError("Invalid price or quantity")
        levels = self.books[symbol][side]
        if q == 0:
            levels.pop(p, None)
        else:
            # Replace the key as well: Kraken checksum uses the latest wire precision.
            levels.pop(p, None)
            levels[p] = q

    def observation(self, symbol: Symbol, event: datetime, received: datetime) -> OrderBook:
        previous = self.last_events.get(symbol)
        if previous is not None and event < previous:
            raise IntegrityError("Exchange event time regressed")
        self.last_events[symbol] = event
        book = self.books[symbol]
        result = OrderBook(
            exchange=self.exchange,
            symbol=symbol,
            bids=tuple(
                Level(price=p, quantity=q)
                for p, q in sorted(book["bids"].items(), reverse=True)[: self.observation_depth]
            ),
            asks=tuple(
                Level(price=p, quantity=q)
                for p, q in sorted(book["asks"].items())[: self.observation_depth]
            ),
            observed_at=event,
            received_at=received,
            synchronized=True,
        )
        self.synchronized_symbols.add(symbol)
        return result

    async def stream(self) -> AsyncGenerator[OrderBook, None]:
        delay = 1.0
        while True:
            self.reset()
            try:
                async with websockets.connect(
                    self.url,
                    open_timeout=15,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=8 * 1024 * 1024,
                    max_queue=16,
                ) as socket:
                    for subscription in self.subscriptions():
                        await socket.send(json.dumps(subscription))
                    started = asyncio.get_running_loop().time()
                    while True:
                        raw = await asyncio.wait_for(socket.recv(), timeout=15)
                        received = datetime.now(UTC)
                        message = json.loads(raw, parse_float=Decimal)
                        for book in self.process(message, received):
                            yield book
                        # Heartbeats never refresh observations. Require initial snapshots.
                        if asyncio.get_running_loop().time() - started > 30 and set(
                            self.books
                        ) != set(self.symbols):
                            raise IntegrityError("Missing initial snapshot")
                        if asyncio.get_running_loop().time() - started > 60:
                            delay = 1.0
            except (asyncio.CancelledError, GeneratorExit):
                # Explicit async-generator close is also a disconnected generation.
                self.reset()
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                self.reconnect_count += 1
                self.reset()
                logger.warning(
                    "market_data_reconnect: %s",
                    self.last_error,
                    extra={"exchange": self.exchange, "reason": str(exc), "retry_seconds": delay},
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
