"""Binance public spot diff-depth; native USDT/USDC remain distinct from USD."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from crossx.adapters.base import IntegrityError, PublicAdapter
from crossx.schemas import OrderBook, Symbol


class BinanceAdapter(PublicAdapter):
    exchange = "binance"
    url = "wss://data-stream.binance.vision/ws"
    allowed_symbols: tuple[Symbol, ...] = (
        "BTC/USD",
        "ETH/USD",
        "BTC/USDT",
        "ETH/USDT",
        "BTC/USDC",
        "ETH/USDC",
    )

    def subscriptions(self) -> list[dict[str, Any]]:
        return [
            {
                "method": "SUBSCRIBE",
                "params": [s.replace("/", "").lower() + "@depth@100ms" for s in self.symbols],
                "id": 1,
            }
        ]

    async def prepare(self) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            for symbol in self.symbols:
                response = await client.get(
                    "https://data-api.binance.vision/api/v3/depth",
                    params={"symbol": symbol.replace("/", ""), "limit": 5000},
                )
                response.raise_for_status()
                self.snapshot(symbol, json.loads(response.text, parse_float=Decimal))

    def reset(self) -> None:
        super().reset()
        self.coverage: dict[Symbol, tuple[Decimal, Decimal]] = {}

    def snapshot(self, symbol: Symbol, data: dict[str, Any]) -> None:
        self.books[symbol] = {"bids": {}, "asks": {}}
        self.sequences[symbol] = int(data["lastUpdateId"])
        for side in ("bids", "asks"):
            for price, quantity in data[side]:
                self.update(symbol, side, price, quantity)
        if not self.books[symbol]["bids"] or not self.books[symbol]["asks"]:
            raise IntegrityError("Empty Binance snapshot")
        self.coverage[symbol] = (min(self.books[symbol]["bids"]), max(self.books[symbol]["asks"]))
        # No exchange time in REST snapshot: do not invent one or emit it.

    def _process(self, message: dict[str, Any], received_at: datetime) -> list[OrderBook]:
        if "code" in message or message.get("e") == "serverShutdown":
            raise IntegrityError("Binance error or server shutdown")
        if "result" in message:
            return []
        if message.get("e") != "depthUpdate":
            raise IntegrityError("Unexpected Binance market frame")
        symbol = next((s for s in self.symbols if s.replace("/", "") == message["s"]), None)
        if symbol not in self.symbols or symbol not in self.books:
            raise IntegrityError("Unrequested market or delta before snapshot")
        first, last = message["U"], message["u"]
        if type(first) is not int or type(last) is not int or first > last:
            raise IntegrityError("Invalid Binance sequence range")
        previous = self.sequences[symbol]
        if last <= previous:
            return []  # Documented obsolete buffered diff after REST snapshot.
        if not first <= previous + 1 <= last:
            raise IntegrityError("Binance depth gap")
        for wire, side in (("b", "bids"), ("a", "asks")):
            for price, quantity in message[wire]:
                self.update(symbol, side, price, quantity)
        if any(len(self.books[symbol][side]) > 20000 for side in ("bids", "asks")):
            raise IntegrityError("Binance reconstruction memory bound reached")
        self.sequences[symbol] = last
        event = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(message["E"]))
        bids = sorted(self.books[symbol]["bids"], reverse=True)
        asks = sorted(self.books[symbol]["asks"])
        floor, ceiling = self.coverage[symbol]
        # Outside the REST-covered range depth completeness is unproven.
        # Require the full exposed depth inside it; otherwise reconnect/snapshot.
        if (
            len(bids) < self.observation_depth
            or len(asks) < self.observation_depth
            or bids[self.observation_depth - 1] < floor
            or asks[self.observation_depth - 1] > ceiling
        ):
            raise IntegrityError("Binance observed depth left proven snapshot coverage")
        book = self.observation(symbol, event, received_at)
        return [book.model_copy(update={"market_id": message["s"]})]
