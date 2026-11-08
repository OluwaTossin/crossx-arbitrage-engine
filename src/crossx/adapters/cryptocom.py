"""Crypto.com Exchange public spot SNAPSHOT_AND_UPDATE with pu/u chaining."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from crossx.adapters.base import IntegrityError, PublicAdapter
from crossx.schemas import OrderBook, Symbol


class CryptoComAdapter(PublicAdapter):
    exchange = "cryptocom"
    url = "wss://stream.crypto.com/exchange/v1/market"
    # The verified instrument endpoint, not symbol similarity, determines availability.
    allowed_symbols: tuple[Symbol, ...] = ("BTC/USD", "ETH/USD", "BTC/USDT", "ETH/USDT")

    def subscriptions(self) -> list[dict[str, Any]]:
        return [
            {
                "id": 1,
                "method": "subscribe",
                "params": {
                    "channels": ["book." + s.replace("/", "_") + ".50" for s in self.symbols],
                    "book_subscription_type": "SNAPSHOT_AND_UPDATE",
                    "book_update_frequency": 100,
                },
            }
        ]

    def response(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if message.get("method") == "public/heartbeat":
            return {"id": message["id"], "method": "public/respond-heartbeat"}
        return None

    def _process(self, message: dict[str, Any], received_at: datetime) -> list[OrderBook]:
        if message.get("code", 0) != 0:
            raise IntegrityError("Crypto.com subscription rejected")
        if message.get("method") == "public/heartbeat":
            return []
        result = message.get("result", {})
        channel = result.get("channel")
        if channel not in ("book", "book.update"):
            return []
        instrument = result["instrument_name"]
        symbol = cast(Symbol, instrument.replace("_", "/"))
        if symbol not in self.symbols:
            raise IntegrityError("Unrequested spot market")
        output = []
        for data in result["data"]:
            sequence = data["u"]
            if type(sequence) is not int:
                raise IntegrityError("Invalid Crypto.com sequence")
            if channel == "book":
                self.books[symbol] = {"bids": {}, "asks": {}}
                self.last_events.pop(symbol, None)
                levels = data
            else:
                if symbol not in self.books or data["pu"] != self.sequences.get(symbol):
                    raise IntegrityError("Crypto.com depth gap or missing snapshot")
                if sequence <= data["pu"]:
                    raise IntegrityError("Crypto.com sequence regressed")
                levels = data["update"]
            for side in ("bids", "asks"):
                for level in levels.get(side, []):
                    self.update(symbol, side, level[0], level[1])
            self.sequences[symbol] = sequence
            event = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(data["t"]))
            book = self.observation(symbol, event, received_at)
            output.append(book.model_copy(update={"market_id": instrument}))
        return output
