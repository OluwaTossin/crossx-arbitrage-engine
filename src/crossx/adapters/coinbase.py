"""Coinbase Advanced Trade public spot level2, using connection sequence continuity."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from crossx.adapters.base import IntegrityError, PublicAdapter, timestamp
from crossx.schemas import OrderBook, Symbol


class CoinbaseAdapter(PublicAdapter):
    exchange = "coinbase"
    url = "wss://advanced-trade-ws.coinbase.com"

    def subscriptions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "subscribe",
                "channel": "level2",
                "product_ids": [s.replace("/", "-") for s in self.symbols],
            },
            {"type": "subscribe", "channel": "heartbeats"},
        ]

    def _process(self, message: dict[str, Any], received_at: datetime) -> list[OrderBook]:
        if message.get("type") == "error":
            raise IntegrityError(f"Coinbase error: {message.get('message')}")
        channel = message.get("channel")
        if "sequence_num" not in message:
            if channel == "l2_data":
                raise IntegrityError("Missing Coinbase sequence")
            return []
        # The public endpoint sequences ALL frames together, including subscription
        # acknowledgements and heartbeats (verified by raw wire replay).
        sequence = message["sequence_num"]
        if not isinstance(sequence, int) or isinstance(sequence, bool):
            raise IntegrityError("Invalid channel sequence")
        previous = self.sequences.get("connection")
        if previous is not None and sequence != previous + 1:
            raise IntegrityError(
                f"Coinbase {channel} sequence gap or reordering: {previous} -> {sequence}"
            )
        self.sequences["connection"] = sequence
        if channel != "l2_data":
            return []
        results = []
        for event in message["events"]:
            symbol = cast(Symbol, event["product_id"].replace("-", "/"))
            if symbol not in self.symbols:
                raise IntegrityError("Unexpected symbol")
            if event["type"] == "snapshot":
                self.books[symbol] = {"bids": {}, "asks": {}}
                self.last_events.pop(symbol, None)
            elif event["type"] != "update" or symbol not in self.books:
                raise IntegrityError("Update before snapshot or invalid event")
            for update in event["updates"]:
                side = {"bid": "bids", "offer": "asks"}.get(update["side"])
                if side is None:
                    raise IntegrityError("Invalid book side")
                self.update(symbol, side, update["price_level"], update["new_quantity"])
                timestamp(update["event_time"])  # Validate engine time; it may precede snapshot.
            # Server publication timestamp identifies this synchronized book observation.
            # Engine event_time can precede the snapshot publication (queued updates).
            observed = timestamp(message["timestamp"])
            results.append(self.observation(symbol, observed, received_at))
        return results
