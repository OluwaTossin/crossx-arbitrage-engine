from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from crossx.config import Settings
from crossx.schemas import FeeSchedule, Level, OrderBook


def test_decimal_serialization_preserves_exact_value() -> None:
    level = Level(price="12345.123456789123456789", quantity="0.000000000123456789")
    assert level.model_dump(mode="json")["price"] == "12345.123456789123456789"
    assert level.quantity == Decimal("0.000000000123456789")


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity"])
def test_invalid_levels_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        Level(price=value, quantity="1")


def test_unknown_fees_stay_unknown() -> None:
    assert FeeSchedule().taker is None
    assert Settings(_env_file=None).fees["coinbase"].taker is None


@pytest.mark.parametrize("value", ["-0.1", "NaN", "Infinity", "1.1"])
def test_invalid_fees_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        FeeSchedule(taker=value)


def test_rejects_naive_time_and_crossed_books() -> None:
    now = datetime.now(UTC)
    kwargs = dict(
        exchange="kraken",
        symbol="BTC/USD",
        bids=(Level(price="101", quantity="1"),),
        asks=(Level(price="100", quantity="1"),),
        observed_at=now,
        received_at=now,
    )
    with pytest.raises(ValidationError):
        OrderBook.model_validate(kwargs)
    kwargs["bids"] = (Level(price="99", quantity="1"),)
    kwargs["observed_at"] = datetime(2026, 1, 1)
    with pytest.raises(ValidationError):
        OrderBook.model_validate(kwargs)


def test_json_environment_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CROSSX_FEES", '{"kraken":{"taker":"0.004"}}')
    monkeypatch.setenv("CROSSX_SYMBOLS", '["BTC/USD"]')
    settings = Settings()
    assert settings.fees["kraken"].taker == Decimal("0.004")
    assert settings.symbols == ("BTC/USD",)
