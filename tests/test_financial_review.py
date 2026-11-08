"""Independent regression tests for financial arithmetic."""

from datetime import UTC, datetime
from decimal import Decimal as D

from crossx.intelligence import detect_opportunities
from crossx.schemas import FeeSchedule, Level, OrderBook


def test_breakeven_depth_does_not_become_profit_after_vwap_roundtrip() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    buy = OrderBook(
        exchange="kraken",
        symbol="BTC/USD",
        bids=(Level(price=D("32"), quantity=D("3")),),
        asks=(Level(price=D("33"), quantity=D("2")), Level(price=D("34"), quantity=D("1"))),
        observed_at=now,
        received_at=now,
    )
    sell = OrderBook(
        exchange="coinbase",
        symbol="BTC/USD",
        bids=(Level(price=D("34"), quantity=D("2")), Level(price=D("33"), quantity=D("1"))),
        asks=(Level(price=D("35"), quantity=D("3")),),
        observed_at=now,
        received_at=now,
    )
    # Exact depth notionals: buy 100, sell 101; buy fee 1, sell fee 0.
    # A repeating VWAP divided by three must not manufacture a positive result.
    assert (
        detect_opportunities(
            [buy, sell],
            {"kraken": FeeSchedule(taker=D(".01")), "coinbase": FeeSchedule(taker=D("0"))},
            D("3"),
            now,
        )
        == []
    )
