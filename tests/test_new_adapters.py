from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crossx.adapters.base import IntegrityError
from crossx.adapters.binance import BinanceAdapter
from crossx.adapters.cryptocom import CryptoComAdapter

T = datetime(2026, 1, 1, tzinfo=UTC)


def binance():
    a = BinanceAdapter(("BTC/USD",))
    a.observation_depth = 1
    a.snapshot("BTC/USD", {"lastUpdateId": 10, "bids": [["99", "1"]], "asks": [["101", "1"]]})
    return a


def delta(**changes):
    v = {
        "e": "depthUpdate",
        "E": int(T.timestamp() * 1000),
        "s": "BTCUSD",
        "U": 11,
        "u": 11,
        "b": [["99", "2"]],
        "a": [],
    }
    v.update(changes)
    return v


def test_binance_snapshot_bridge_delete_gap_reset():
    a = binance()
    assert a.process(delta(U=9, u=10), T) == []
    result = a.process(delta(U=9, u=12), T)
    assert result[0].bids[0].quantity == Decimal(2)
    assert result[0].quote_asset == "USD"
    assert result[0].market_id == "BTCUSD"
    generation = a.generation
    with pytest.raises(IntegrityError, match="gap"):
        a.process(delta(U=14, u=14), T)
    assert a.generation > generation and not a.synchronized_symbols


def test_binance_no_snapshot_unexpected_quote_and_float():
    a = BinanceAdapter(("BTC/USD",))
    with pytest.raises(IntegrityError):
        a.process(delta(), T)
    a = binance()
    with pytest.raises(IntegrityError):
        a.process(delta(s="BTCUSDT"), T)
    a = binance()
    with pytest.raises(IntegrityError):
        a.process(delta(b=[["99", 2.1]]), T)


def crypto(channel="book", **data):
    d = {
        "u": 1,
        "t": int(T.timestamp() * 1000),
        "bids": [["99", "1", "1"]],
        "asks": [["101", "1", "1"]],
    }
    d.update(data)
    return {"code": 0, "result": {"channel": channel, "instrument_name": "BTC_USD", "data": [d]}}


def test_cryptocom_snapshot_delta_chain_and_heartbeat():
    a = CryptoComAdapter(("BTC/USD",))
    assert a.response({"method": "public/heartbeat", "id": 7}) == {
        "method": "public/respond-heartbeat",
        "id": 7,
    }
    b = a.process(crypto(), T)[0]
    assert b.market_id == "BTC_USD"
    b = a.process(crypto("book.update", u=2, pu=1, update={"bids": [["99", "0"], ["98", "2"]]}), T)[
        0
    ]
    assert b.bids[0].price == 98
    with pytest.raises(IntegrityError):
        a.process(crypto("book.update", u=4, pu=3, update={}), T)
    assert not a.synchronized_symbols


def test_cryptocom_update_before_snapshot_and_error():
    a = CryptoComAdapter(("BTC/USD",))
    with pytest.raises(IntegrityError):
        a.process(crypto("book.update", u=2, pu=1, update={}), T)
    with pytest.raises(IntegrityError):
        a.process({"code": 10004}, T)


def test_binance_rest_coverage_exhaustion_fails_closed():
    a = binance()
    with pytest.raises(IntegrityError, match="coverage"):
        a.process(delta(b=[["99", "0"], ["98", "1"]]), T)
    assert not a.synchronized_symbols


def test_new_adapter_cancel_invalidates_generation():
    a = binance()
    a.process(delta(), T)
    generation = a.generation
    a.reset()
    assert a.generation > generation
    assert a.synchronized_symbols == set()
    assert a.coverage == {}
