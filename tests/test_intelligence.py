from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from crossx.intelligence import detect_opportunities
from crossx.schemas import FeeSchedule, Level, OrderBook

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def book(exchange="kraken", bids=(("99", "2"),), asks=(("100", "2"),), **kwargs):
    return OrderBook(
        exchange=exchange,
        symbol=kwargs.pop("symbol", "BTC/USD"),
        bids=tuple(Level(price=D(p), quantity=D(q)) for p, q in bids),
        asks=tuple(Level(price=D(p), quantity=D(q)) for p, q in asks),
        observed_at=kwargs.pop("observed_at", NOW),
        received_at=kwargs.pop("received_at", NOW),
        **kwargs,
    )


def pair():
    return [book(), book("coinbase", bids=(("110", "2"),), asks=(("111", "2"),))]


def fees(rate="0.01"):
    return {venue: FeeSchedule(taker=D(rate)) for venue in ("kraken", "coinbase")}


def detect(books=None, **kwargs):
    return detect_opportunities(
        pair() if books is None else books, kwargs.pop("fees", fees()), D("1"), NOW, **kwargs
    )


def test_full_notional_taker_fees():
    (result,) = detect()
    assert result.buy_exchange == "kraken"
    assert result.sell_exchange == "coinbase"
    assert result.buy_vwap == D("100")
    assert result.sell_vwap == D("110")
    assert result.gross_profit == D("10")
    assert result.buy_fee == D("1")
    assert result.sell_fee == D("1.1")
    assert result.net_profit == D("7.9")


def test_direction_reverses_with_prices():
    books = pair()
    books = [
        books[0].model_copy(update={"exchange": "coinbase"}),
        books[1].model_copy(update={"exchange": "kraken"}),
    ]
    (result,) = detect(books)
    assert result.buy_exchange == "coinbase"
    assert result.sell_exchange == "kraken"


def test_vwap_consumes_multiple_levels_on_each_leg():
    books = [
        book(asks=(("100", ".25"), ("104", ".75"))),
        book("coinbase", bids=(("110", ".5"), ("108", ".5")), asks=(("111", "2"),)),
    ]
    (result,) = detect(books, fees=fees("0"))
    assert result.buy_vwap == D("103")
    assert result.sell_vwap == D("109")
    assert result.net_profit == D("6")


@pytest.mark.parametrize("side", ["bids", "asks"])
def test_insufficient_liquidity_rejects(side):
    books = pair()
    index = 0 if side == "asks" else 1
    books[index] = books[index].model_copy(
        update={side: (Level(price=D("105"), quantity=D(".99")),)}
    )
    assert detect(books) == []


@pytest.mark.parametrize(
    "schedule",
    [
        {},
        {"kraken": FeeSchedule(taker=D("0"))},
        {"kraken": FeeSchedule(maker=D("0")), "coinbase": FeeSchedule(taker=D("0"))},
    ],
)
def test_unknown_fees_never_become_zero(schedule):
    assert detect(fees=schedule) == []


def test_fee_cost_can_eliminate_positive_gross_spread():
    assert detect(fees=fees("0.1")) == []


def test_threshold_is_strict():
    assert detect(min_net_profit=D("7.9")) == []
    assert len(detect(min_net_profit=D("7.89"))) == 1


@pytest.mark.parametrize("field", ["observed_at", "received_at"])
@pytest.mark.parametrize("offset", [-6, 0.001])
def test_stale_and_future_timestamps_reject(field, offset):
    books = pair()
    books[0] = books[0].model_copy(update={field: NOW + timedelta(seconds=offset)})
    assert detect(books) == []


def test_stale_boundary_accepted_with_matching_books():
    books = [
        item.model_copy(
            update={
                "observed_at": NOW - timedelta(seconds=5),
                "received_at": NOW - timedelta(seconds=5),
            }
        )
        for item in pair()
    ]
    assert len(detect(books)) == 1


def test_event_skew_rejects_individually_fresh_books():
    books = pair()
    books[0] = books[0].model_copy(update={"observed_at": NOW - timedelta(seconds=3)})
    assert detect(books) == []


def test_unsynchronized_book_rejects():
    books = pair()
    books[0] = books[0].model_copy(update={"synchronized": False})
    assert detect(books) == []


def test_cross_symbol_and_duplicate_observations_reject():
    books = pair()
    assert detect([books[0], books[1].model_copy(update={"symbol": "ETH/USD"})]) == []
    assert detect([*books, books[0]]) == []
    assert detect([]) == []
    assert detect([books[0]]) == []


@pytest.mark.parametrize("quantity", ["0", "-1", "NaN", "Infinity"])
def test_invalid_quantity(quantity):
    with pytest.raises(ValueError):
        detect_opportunities(pair(), fees(), D(quantity), NOW)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_age_seconds": -1},
        {"max_skew_seconds": float("inf")},
        {"min_net_profit": D("NaN")},
        {"min_net_profit": D("-1")},
    ],
)
def test_invalid_policy(kwargs):
    with pytest.raises(ValueError):
        detect(**kwargs)


def test_naive_now_rejected():
    with pytest.raises(ValueError):
        detect_opportunities(pair(), fees(), D("1"), NOW.replace(tzinfo=None))


def test_provenance_records_exact_book_pair_and_fee_assumptions():
    books = pair()
    books[0] = books[0].model_copy(
        update={
            "observed_at": NOW - timedelta(seconds=1),
            "received_at": NOW - timedelta(milliseconds=500),
        }
    )
    schedules = {"kraken": FeeSchedule(taker=D(".01")), "coinbase": FeeSchedule(taker=D(".02"))}
    (result,) = detect(books, fees=schedules)
    assert result.buy_book_observed_at == books[0].observed_at
    assert result.sell_book_observed_at == books[1].observed_at
    assert result.buy_book_received_at == books[0].received_at
    assert result.sell_book_received_at == books[1].received_at
    assert result.buy_taker_rate == D(".01")
    assert result.sell_taker_rate == D(".02")
    assert result.detected_at == NOW
    assert result.net_profit == D("6.8")


@pytest.mark.parametrize(
    "field",
    [
        "buy_book_observed_at",
        "sell_book_observed_at",
        "buy_book_received_at",
        "sell_book_received_at",
        "detected_at",
    ],
)
def test_provenance_rejects_naive_timestamps(field):
    from pydantic import ValidationError

    from crossx.schemas import Opportunity

    (result,) = detect()
    values = result.model_dump()
    values[field] = NOW.replace(tzinfo=None)
    with pytest.raises(ValidationError, match="timezone aware"):
        Opportunity(**values)


@pytest.mark.parametrize("field", ["buy_taker_rate", "sell_taker_rate"])
@pytest.mark.parametrize("rate", ["-0.1", "1.01", "NaN"])
def test_provenance_rejects_invalid_fee_rates(field, rate):
    from pydantic import ValidationError

    from crossx.schemas import Opportunity

    (result,) = detect()
    values = result.model_dump()
    values[field] = D(rate)
    with pytest.raises(ValidationError):
        Opportunity(**values)


def test_provenance_fields_are_optional_for_earlier_observations():
    from crossx.schemas import Opportunity

    (result,) = detect()
    values = result.model_dump()
    for field in [
        "buy_book_observed_at",
        "sell_book_observed_at",
        "buy_book_received_at",
        "sell_book_received_at",
        "buy_taker_rate",
        "sell_taker_rate",
        "detected_at",
    ]:
        del values[field]
    restored = Opportunity(**values)
    assert restored.detected_at is None
    assert restored.buy_taker_rate is None


def test_negative_config_profit_threshold_rejected():
    from pydantic import ValidationError

    from crossx.config import Settings

    with pytest.raises(ValidationError):
        Settings(min_net_profit=D("-0.01"))
