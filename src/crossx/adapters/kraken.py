"""Kraken public WebSocket v2 books, with mandatory top-ten CRC32 validation."""

from __future__ import annotations

import zlib
from datetime import datetime
from typing import Any, cast

from crossx.adapters.base import IntegrityError, PublicAdapter, timestamp
from crossx.schemas import OrderBook, Symbol


class KrakenAdapter(PublicAdapter):
    exchange = "kraken"
    url = "wss://ws.kraken.com/v2"
    depth = 25

    def subscriptions(self) -> list[dict[str, Any]]:
        return [
            {
                "method": "subscribe",
                "params": {
                    "channel": "book",
                    "symbol": list(self.symbols),
                    "depth": self.depth,
                    "snapshot": True,
                },
            }
        ]

    def checksum(self, symbol: str) -> int:
        book = self.books[symbol]
        chunks: list[str] = []
        for side, reverse in (("asks", False), ("bids", True)):
            for price, qty in sorted(book[side].items(), reverse=reverse)[:10]:
                chunks.extend(
                    format(value, "f").replace(".", "").lstrip("0") for value in (price, qty)
                )
        return zlib.crc32("".join(chunks).encode()) & 0xFFFFFFFF

    def _process(self, message: dict[str, Any], received_at: datetime) -> list[OrderBook]:
        if message.get("success") is False:
            raise IntegrityError(f"Subscription rejected: {message.get('error')}")
        if message.get("channel") == "status":
            if any(item.get("system") != "online" for item in message["data"]):
                raise IntegrityError("Kraken market system not online")
            return []
        if message.get("channel") != "book":
            return []
        kind = message["type"]
        if kind not in ("snapshot", "update"):
            raise IntegrityError("Unknown Kraken book event")
        results = []
        for item in message["data"]:
            symbol = cast(Symbol, item["symbol"])
            if symbol not in self.symbols:
                raise IntegrityError("Unexpected symbol")
            if kind == "snapshot":
                self.books[symbol] = {"bids": {}, "asks": {}}
            elif symbol not in self.books:
                raise IntegrityError("Update before snapshot")
            for side in ("bids", "asks"):
                for level in item.get(side, []):
                    self.update(symbol, side, level["price"], level["qty"])
                self.books[symbol][side] = dict(
                    sorted(self.books[symbol][side].items(), reverse=side == "bids")[: self.depth]
                )
            if self.checksum(symbol) != item["checksum"]:
                raise IntegrityError("Kraken checksum mismatch")
            if kind == "update" and "timestamp" not in item:
                raise IntegrityError("Missing Kraken update timestamp")
            event = timestamp(item["timestamp"]) if "timestamp" in item else received_at
            # A snapshot without a server timestamp cannot establish exchange-time ordering.
            if kind == "snapshot":
                self.last_events.pop(symbol, None)
            results.append(self.observation(symbol, event, received_at))
            if "timestamp" not in item:
                self.last_events.pop(symbol, None)
        return results
