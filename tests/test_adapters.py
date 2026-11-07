import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from crossx.adapters import CoinbaseAdapter, KrakenAdapter
from crossx.adapters.base import IntegrityError

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)


def replay():
    return json.loads((Path(__file__).parent / "fixtures/coinbase_replay.json").read_text())


def kraken_snapshot(adapter):
    adapter.books["BTC/USD"] = {
        "bids": {Decimal("100.10"): Decimal("2.0")},
        "asks": {Decimal("101.20"): Decimal("3.0")},
    }
    checksum = adapter.checksum("BTC/USD")
    adapter.reset()
    return {
        "channel": "book",
        "type": "snapshot",
        "data": [
            {
                "symbol": "BTC/USD",
                "bids": [{"price": "100.10", "qty": "2.0"}],
                "asks": [{"price": "101.20", "qty": "3.0"}],
                "checksum": checksum,
            }
        ],
    }


def test_coinbase_replay_absolute_sizes_deletion():
    adapter = CoinbaseAdapter(("BTC/USD",))
    snapshot, update = replay()
    first = adapter.process(snapshot, NOW)[0]
    result = adapter.process(update, NOW)[0]
    assert first.bids[0].quantity == Decimal("2.0")
    assert result.bids[0].price == Decimal("100.20")
    assert result.bids[0].quantity == Decimal("1.25")
    assert adapter.synchronized_symbols == {"BTC/USD"}


@pytest.mark.parametrize("sequence", [0, 3])
def test_coinbase_gap_and_duplicate_reset(sequence):
    adapter = CoinbaseAdapter(("BTC/USD",))
    snapshot, update = replay()
    adapter.process(snapshot, NOW)
    update["sequence_num"] = sequence
    with pytest.raises(IntegrityError):
        adapter.process(update, NOW)
    assert not adapter.books and not adapter.synchronized_symbols


def test_update_before_snapshot():
    adapter = CoinbaseAdapter(("BTC/USD",))
    with pytest.raises(IntegrityError):
        adapter.process(replay()[1], NOW)


def test_heartbeat_does_not_refresh_book():
    adapter = CoinbaseAdapter(("BTC/USD",))
    adapter.process(replay()[0], NOW)
    assert adapter.process({"channel": "heartbeats", "sequence_num": 1}, NOW) == []
    assert adapter.sequences["connection"] == 1
    assert adapter.last_events["BTC/USD"] == NOW


def test_kraken_checksum_precision_and_mismatch():
    adapter = KrakenAdapter(("BTC/USD",))
    message = kraken_snapshot(adapter)
    book = adapter.process(message, NOW)[0]
    assert book.observed_at == NOW
    message["data"][0]["checksum"] += 1
    with pytest.raises(IntegrityError, match="checksum"):
        adapter.process(message, NOW)
    assert not adapter.synchronized_symbols


def test_kraken_update_truncate_delete_and_repeated_price():
    adapter = KrakenAdapter(("BTC/USD",))
    adapter.process(kraken_snapshot(adapter), NOW)
    levels = [
        {"price": "100.10", "qty": "0"},
        {"price": "100.20", "qty": "1"},
        {"price": "100.20", "qty": "4.000"},
    ]
    for level in levels:
        adapter.update("BTC/USD", "bids", level["price"], level["qty"])
    crc = adapter.checksum("BTC/USD")
    adapter.process(kraken_snapshot(adapter), NOW)
    result = adapter.process(
        {
            "channel": "book",
            "type": "update",
            "data": [
                {
                    "symbol": "BTC/USD",
                    "timestamp": "2026-11-07T12:00:01Z",
                    "bids": levels,
                    "checksum": crc,
                }
            ],
        },
        NOW,
    )[0]
    assert len(result.bids) == 1 and result.bids[0].quantity == Decimal("4.000")


@pytest.mark.parametrize("bad", ["NaN", "-1", "Infinity"])
def test_invalid_quantities_reset(bad):
    adapter = CoinbaseAdapter(("BTC/USD",))
    snapshot = replay()[0]
    snapshot["events"][0]["updates"][0]["new_quantity"] = bad
    with pytest.raises(IntegrityError):
        adapter.process(snapshot, NOW)
    assert not adapter.books


def test_crossed_book_rejected():
    adapter = CoinbaseAdapter(("BTC/USD",))
    snapshot = replay()[0]
    snapshot["events"][0]["updates"][0]["price_level"] = "999"
    with pytest.raises(IntegrityError):
        adapter.process(snapshot, NOW)


def test_float_rejected_for_checksum_safety():
    adapter = KrakenAdapter(("BTC/USD",))
    message = kraken_snapshot(adapter)
    message["data"][0]["bids"][0]["price"] = 100.1
    with pytest.raises(IntegrityError, match="Float"):
        adapter.process(message, NOW)


@pytest.mark.asyncio
async def test_reconnect_clears_state_before_backoff():
    adapter = CoinbaseAdapter(("BTC/USD",))
    adapter.process(replay()[0], NOW)

    async def cancel_sleep(delay):
        assert not adapter.synchronized_symbols
        raise asyncio.CancelledError

    with (
        patch("crossx.adapters.base.websockets.connect", side_effect=OSError("offline")),
        patch("crossx.adapters.base.asyncio.sleep", side_effect=cancel_sleep),
    ):
        with pytest.raises(asyncio.CancelledError):
            await anext(adapter.stream())
    assert adapter.reconnect_count == 1


def test_kraken_official_checksum_vector():
    # https://docs.kraken.com/exchange/guides/websockets/book-checksum-v2
    adapter = KrakenAdapter(("BTC/USD",))
    bids = [
        ("45283.5", "0.10000000"),
        ("45283.4", "1.54582015"),
        ("45282.1", "0.10000000"),
        ("45281.0", "0.10000000"),
        ("45280.3", "1.54592586"),
        ("45279.0", "0.07990000"),
        ("45277.6", "0.03310103"),
        ("45277.5", "0.30000000"),
        ("45277.3", "1.54602737"),
        ("45276.6", "0.15445238"),
    ]
    asks = [
        ("45285.2", "0.00100000"),
        ("45286.4", "1.54571953"),
        ("45286.6", "1.54571109"),
        ("45289.6", "1.54560911"),
        ("45290.2", "0.15890660"),
        ("45291.8", "1.54553491"),
        ("45294.7", "0.04454749"),
        ("45296.1", "0.35380000"),
        ("45297.5", "0.09945542"),
        ("45299.5", "0.18772827"),
    ]
    adapter.books["BTC/USD"] = {
        side: {Decimal(p): Decimal(q) for p, q in rows}
        for side, rows in (("bids", bids), ("asks", asks))
    }
    assert adapter.checksum("BTC/USD") == 3310070434


def test_coinbase_queued_engine_time_precedes_snapshot_publication():
    adapter = CoinbaseAdapter(("BTC/USD",))
    snapshot, update = replay()
    adapter.process(snapshot, NOW)
    for change in update["events"][0]["updates"]:
        change["event_time"] = "2026-11-07T11:59:59Z"
    assert adapter.process(update, NOW)[0].observed_at > NOW


def test_observations_bound_depth_without_truncating_coinbase_state():
    adapter = CoinbaseAdapter(("BTC/USD",))
    snapshot = replay()[0]
    snapshot["events"][0]["updates"] = [
        {
            "side": side,
            "price_level": str(price),
            "new_quantity": "1",
            "event_time": "1970-01-01T00:00:00Z",
        }
        for side, prices in (("bid", range(1, 51)), ("offer", range(60, 110)))
        for price in prices
    ]
    book = adapter.process(snapshot, NOW)[0]
    assert len(book.bids) == len(book.asks) == 25
    assert len(adapter.books["BTC/USD"]["bids"]) == 50


def test_coinbase_sequence_includes_acknowledgements_and_heartbeats():
    adapter = CoinbaseAdapter(("BTC/USD",))
    snapshot, update = replay()
    adapter.process(snapshot, NOW)
    assert adapter.process({"channel": "subscriptions", "sequence_num": 1}, NOW) == []
    assert adapter.process({"channel": "heartbeats", "sequence_num": 2}, NOW) == []
    update["sequence_num"] = 3
    assert adapter.process(update, NOW)[0].bids[0].quantity == Decimal("1.25")
    update["sequence_num"] = 5
    with pytest.raises(IntegrityError, match="sequence gap"):
        adapter.process(update, NOW)
